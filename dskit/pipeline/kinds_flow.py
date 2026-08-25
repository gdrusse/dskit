"""The ordinary flow kinds — ``filter``, ``event-bank``, ``eligibility``,
``banking-report`` (spec §10 step 3) — and the RELATIONAL kinds
``concat``, ``join``, ``derive``, which combine streams instead of
reading one.

These are the toolkit's plain, un-owned kinds: the record filter, the
★BANKING counter, the admission gate, and the banking ledger — the
``bank -> eligible_family -> banking_report`` spine of
``examples/pipeline/PROPOSAL-node-map.jsonc``. They are registered with
``owned=False`` (any project may shadow them with its own class via an
import path); the doctrine kinds (``stat_test``, ``validate``) are a
separate, owned matter.

The relational three are the vocabulary a document needs before two
data sources can share ONE bankroll: without a verb that says "and
also", two venues are two documents, two ``replay`` nodes and two
``final_bankroll`` numbers, and any allocation between them is
arithmetic performed after the fact on runs that never competed. They
are venue-blind by construction and take N inputs, never two (D-137: no
venue is privileged, and nothing here may special-case a pair).

Record tolerance — one rule for the four single-stream kinds: records
flowing through them are either plain dicts or objects with attributes
(e.g. :class:`~dskit.pipeline.records.MarketRecord`), and every field
access goes through the single :func:`_field` accessor. A record that
lacks a needed field is DROPPED or SKIPPED, never crashed on — sparse
records are data, not shape errors (each class documents its exact
semantics). The relational three deliberately do the OPPOSITE and
REFUSE; the section comment above :class:`Concat` says why, and every
relaxation has to be declared in the document before it is allowed.

Validator convention: params may legally arrive as unmaterialized
``$``-references at plan time (``"$splits.train_end_ms"`` is the
designed use for ``strictly_before``); validators tolerate the
``$``-form and check the real value when construction sees the
materialized params. ``$prev`` carries never reach a validator raw —
the planner substitutes their literal defaults first.

No import-time side effects: nothing registers until :func:`register`
is called.

Import cost: stdlib only.
"""

from __future__ import annotations

import copy
import itertools
import re

from dskit.pipeline.document import is_node_ref
from dskit.pipeline.kinds_stats import _reject_unknown
from dskit.pipeline.node import DEFAULT_NODE_KINDS, Node

__all__ = [
    "BankingReport",
    "Concat",
    "Derive",
    "Eligibility",
    "EventBank",
    "Filter",
    "Join",
    "register",
]

#: Sentinel for "the record carries no such field" — distinct from every
#: real value a field could hold (``None`` included).
_MISSING = object()

#: What ``event-bank`` may count DISTINCT occurrences of.
#:
#: ``"group"`` is the DEFAULT (I-224): one EVENT counts once, however many
#: times it was observed. Identity is the record's ``group`` — the
#: statistical-dependence cluster :class:`~dskit.pipeline.records.MarketRecord`
#: defines — falling back to ``contract`` when ``group`` is ``None`` or
#: absent, which is that envelope's own stated meaning ("the contract is
#: its own cluster"). ``"contract"`` counts distinct tradeable units.
#: ``"record"`` counts every input record and is the ONLY way to ask for
#: that: it must be declared, because a counter feeding a
#: ``min_events`` bar may never over-count by omission.
_DISTINCT_FIELDS = ("group", "contract", "record")


def _field(record, name):
    """THE attr-or-key accessor: ``record[name]`` for mappings,
    ``record.name`` for objects, :data:`_MISSING` when absent. Every
    record field read in this module goes through here — dicts and
    :class:`~dskit.pipeline.records.MarketRecord`-like objects are
    interchangeable by construction."""
    if isinstance(record, dict):
        return record.get(name, _MISSING)
    return getattr(record, name, _MISSING)


def _require_min_events(problems, params) -> None:
    """The stated admission bar: ``min_events`` is REQUIRED (no default —
    the bar must be stated, never assumed) and must be an int >= 1."""
    if "min_events" not in params:
        problems.append(
            "min_events is required — the admission bar must be stated "
            "explicitly, there is no default"
        )
        return
    bar = params["min_events"]
    if isinstance(bar, bool) or not isinstance(bar, int) or bar < 1:
        problems.append(f"min_events must be an int >= 1, got {bar!r}")


def _counts_problems(port, counts):
    """Problems with a banked-counts input: a ``{instrument: n}`` dict
    with int counts >= 0 (bools excluded), empty when none."""
    if not isinstance(counts, dict):
        return [f"{port} must be a counts dict ({{instrument: n}}), got {counts!r}"]
    return [
        f"{port}[{instrument!r}] must be an int >= 0, got {n!r}"
        for instrument, n in counts.items()
        if isinstance(n, bool) or not isinstance(n, int) or n < 0
    ]


# ---------------------------------------------------------------------------
# filter — the record filter (role: transform)
# ---------------------------------------------------------------------------

#: The known where-clause operators. ``in`` tests membership of the
#: record's value in the clause's value.
_OPS = {
    "==": lambda value, target: value == target,
    "!=": lambda value, target: value != target,
    ">": lambda value, target: value > target,
    "<": lambda value, target: value < target,
    ">=": lambda value, target: value >= target,
    "<=": lambda value, target: value <= target,
    "in": lambda value, target: value in target,
}


def _clause_problems(name, clause):
    """Shape problems with one where clause, empty when none."""
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
    if clause.get("op") not in _OPS:
        problems.append(
            f"{name}.op must be one of {sorted(_OPS)}, got {clause.get('op')!r}"
        )
    return problems


def _clause_holds(record, clause) -> bool:
    """Whether ``record`` passes one where clause. A clause can only PASS
    on a present, comparable field: a missing field fails it (even under
    ``!=``), and so does an incomparable pair (``TypeError`` — e.g. a
    string against an int bound, or ``in`` against a non-container)."""
    value = _field(record, clause["field"])
    if value is _MISSING:
        return False
    try:
        return bool(_OPS[clause["op"]](value, clause["value"]))
    except TypeError:
        return False


class Filter(Node):
    """Keep the records that pass every declared condition (role
    ``transform``) — the toolkit's ``filter`` kind.

    Inputs: ``records`` (dicts or record objects); OPTIONAL
    ``instruments`` — an allow-list, keeping only records whose
    ``instrument`` is in it (the PROPOSAL wires the eligible family
    here, so the universe grows with zero config edits).

    Params: ``require_usable`` (bool, default False) drops records whose
    ``usable`` field is falsy; ``where`` is a list of
    ``{"field", "op", "value"}`` clauses over :data:`_OPS`, ALL of which
    must hold.

    Sparse-record semantics, by design: a record missing a tested field
    (or carrying an incomparable value) is DROPPED — a filter never
    crashes on sparse records, and it never passes a record it could not
    actually test. That includes ``require_usable`` (no ``usable`` field
    = cannot claim usability = dropped) and the allow-list (no
    ``instrument`` field = cannot prove membership = dropped).

    Input order is preserved; kept/total is logged.
    """

    role = "transform"
    outputs = ("records",)

    #: The class's own knobs — anything else is refused by name.
    _PARAMS = ("require_usable", "where")

    @classmethod
    def validate_params(cls, params):
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
            problems.extend(_clause_problems(f"where[{i}]", clause))
        return problems

    def validate_inputs(self, inputs):
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
            if all(_clause_holds(record, clause) for clause in where):
                kept.append(record)
        self.log.info("filter kept %d/%d record(s)", len(kept), len(records))
        return {"records": kept}


# ---------------------------------------------------------------------------
# event-bank — the ★BANKING counter (role: accrual)
# ---------------------------------------------------------------------------


class EventBank(Node):
    """★BANKING: count banked events per instrument (role ``accrual``) —
    the toolkit's ``event-bank`` kind, the week-over-week
    ``12 -> 27 -> 43 -> 51`` counter the admission gate reads.

    Inputs: ``events`` — records carrying ``instrument``, ``contract``,
    ``asof_ms``; OPTIONAL ``outcomes`` — ``{contract: settled-YES?}``,
    whose KEYS mark settledness (a settled NO is settled: presence in
    the dict is what counts, the bool is the direction).

    Params: ``count`` — ``"settled"`` (default; count only events whose
    contract is present in ``outcomes``, which the input contract
    therefore requires at execute) or ``"all"``; ``strictly_before`` —
    optional int epoch-ms cut, arriving pre-materialized (the designed
    wiring is ``"$splits.train_end_ms"``-style references). Knowable-
    at-T1 doctrine: only events with ``asof_ms`` STRICTLY below the cut
    count — an event at or past the cut is never banked.

    Sparse-record semantics: an event missing ``instrument`` or
    ``contract``, or whose ``asof_ms`` is not an int, cannot prove where
    (or when) it banks and is SKIPPED, never crashed on; skips are
    logged.

    Outputs: ``counts`` — ``{instrument: n}`` DISTINCT events (see
    ``distinct_by``); ``extents`` — ``{instrument: {"first_ms",
    "last_ms"}}`` over every OBSERVATION that fed those counts, not over
    the first sighting of each event, so the extent stays the honest
    span of the banked data.
    """

    role = "accrual"
    outputs = ("counts", "extents")

    #: ``distinct_by`` — WHAT one "event" is in the stream you wired in.
    #: DEFAULT ``"group"`` (I-224, 2026-08-15): one event counts ONCE
    #: however many times it was observed. A market ladder carries every
    #: lead time of every strike contract, so counting records reports a
    #: multiple of the truth and a ``min_events`` gate opens on a fraction
    #: of the evidence it names — the over-count is silent, and the gate
    #: it feeds decides which markets get tested at all, so the default is
    #: the one that CANNOT overstate evidence. ``"contract"`` counts
    #: distinct tradeable units; ``"record"`` counts every input record
    #: (right when one record IS one event) and must be said out loud.

    #: The class's own knobs — anything else is refused by name.
    _PARAMS = ("count", "distinct_by", "strictly_before")

    @classmethod
    def validate_params(cls, params):
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        count = params.get("count", "settled")
        if count not in ("settled", "all"):
            problems.append(f"count must be 'settled' or 'all', got {count!r}")
        cut = params.get("strictly_before")
        if (
            cut is not None
            and not is_node_ref(cut)
            and (isinstance(cut, bool) or not isinstance(cut, int))
        ):
            problems.append(
                f"strictly_before must be an int epoch-ms (or a $-reference "
                f"that materializes to one), got {cut!r}"
            )
        distinct_by = params.get("distinct_by")
        if distinct_by is not None and distinct_by not in _DISTINCT_FIELDS:
            problems.append(
                f"distinct_by must be one of {list(_DISTINCT_FIELDS)} (or "
                f"absent, defaulting to 'group' — one event counted once), "
                f"got {distinct_by!r}"
            )
        return problems

    def validate_inputs(self, inputs):
        problems = []
        if not isinstance(inputs.get("events"), list):
            problems.append(
                f"events must be a list of records, got {inputs.get('events')!r}"
            )
        if self.params.get("count", "settled") == "settled" and not isinstance(
            inputs.get("outcomes"), dict
        ):
            problems.append(
                "count='settled' requires the outcomes input "
                f"({{contract: settled-YES?}}) to know which events are "
                f"settled, got {inputs.get('outcomes')!r}"
            )
        return problems

    def run(self, ctx, inputs):
        count_settled = self.params.get("count", "settled") == "settled"
        cut = self.params.get("strictly_before")
        distinct_by = self.params.get("distinct_by", "group")
        outcomes = inputs.get("outcomes")
        counts = {}
        extents = {}
        seen = set()
        skipped = 0
        observations = 0
        for event in inputs["events"]:
            instrument = _field(event, "instrument")
            contract = _field(event, "contract")
            asof_ms = _field(event, "asof_ms")
            if (
                instrument is _MISSING
                or contract is _MISSING
                or isinstance(asof_ms, bool)
                or not isinstance(asof_ms, int)
            ):
                skipped += 1
                continue
            if cut is not None and asof_ms >= cut:
                continue  # knowable-at-T1: at-or-past the cut never counts
            if count_settled and contract not in outcomes:
                continue
            # Every observation that survived the filters widens the extent,
            # whether or not it is the first sighting of its event — the
            # extent describes the DATA, the counts describe the EVENTS.
            observations += 1
            extent = extents.get(instrument)
            if extent is None:
                extents[instrument] = {"first_ms": asof_ms, "last_ms": asof_ms}
            else:
                extent["first_ms"] = min(extent["first_ms"], asof_ms)
                extent["last_ms"] = max(extent["last_ms"], asof_ms)
            if distinct_by != "record":
                identity = contract
                if distinct_by == "group":
                    # group is the cluster id; None (or absent) is the
                    # envelope's own "this contract is its own cluster",
                    # NOT one shared bucket every record collapses into.
                    group = _field(event, "group")
                    if group is not _MISSING and group is not None:
                        identity = group
                if (instrument, identity) in seen:
                    continue
                seen.add((instrument, identity))
            counts[instrument] = counts.get(instrument, 0) + 1
        self.log.info(
            "banked %d event(s) across %d instrument(s) from %d observation(s) "
            "(distinct_by=%s, %d skipped as malformed)",
            sum(counts.values()),
            len(counts),
            observations,
            distinct_by,
            skipped,
        )
        return {"counts": counts, "extents": extents}


# ---------------------------------------------------------------------------
# eligibility — the admission gate (role: gate)
# ---------------------------------------------------------------------------


class Eligibility(Node):
    """The admission bar (role ``gate``) — the toolkit's ``eligibility``
    kind: instruments whose banked count clears ``min_events`` form the
    family; an empty family is a NO-GO, and THAT is this node's point —
    the driver halts every DAG descendant on the verdict, so nothing
    downstream ever runs on an inadmissible universe.

    Inputs: ``banked`` — the ``event-bank`` counts dict. Params:
    ``min_events`` — REQUIRED int >= 1, no default: the admission bar
    must be stated, never assumed.

    Outputs: ``instruments`` — the sorted family; ``verdict`` — ``"GO"``
    iff the family is non-empty, else ``"NO-GO"``.
    """

    role = "gate"
    outputs = ("instruments", "verdict")

    #: The class's own knobs — anything else is refused by name.
    _PARAMS = ("min_events",)

    @classmethod
    def validate_params(cls, params):
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        _require_min_events(problems, params)
        return problems

    def validate_inputs(self, inputs):
        return _counts_problems("banked", inputs.get("banked"))

    def run(self, ctx, inputs):
        bar = self.params["min_events"]
        banked = inputs["banked"]
        family = sorted(instrument for instrument, n in banked.items() if n >= bar)
        verdict = "GO" if family else "NO-GO"
        self.log.info(
            "eligibility: %d/%d instrument(s) >= %d — %s",
            len(family),
            len(banked),
            bar,
            verdict,
        )
        return {"instruments": family, "verdict": verdict}


# ---------------------------------------------------------------------------
# banking-report — the weekly ledger (role: report)
# ---------------------------------------------------------------------------


class BankingReport(Node):
    """The banking ledger (role ``report``) — the toolkit's
    ``banking-report`` kind: who is IN the family, who is pending at
    43/50, and how far each has to go.

    Inputs: ``banked`` (counts), ``family`` (the eligible instruments),
    OPTIONAL ``extents`` (per-instrument first/last banked ms). Params:
    ``min_events`` — REQUIRED int >= 1, the same stated bar the gate
    applied.

    Writes ``banking.json`` into this node's artifact dir: per
    instrument ``{banked, in_family, gap}`` with
    ``gap = max(0, min_events - n)`` (plus ``first_ms``/``last_ms`` when
    extents are wired), and totals. Rows cover the union of banked and
    family instruments, so a family member with no counts row still
    appears (banked 0) instead of vanishing from the ledger.

    Outputs: ``path`` — the artifact; ``summary`` — ``{"in": k,
    "pending": m}``.
    """

    role = "report"
    outputs = ("path", "summary")

    #: The class's own knobs — anything else is refused by name.
    _PARAMS = ("min_events",)

    @classmethod
    def validate_params(cls, params):
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        _require_min_events(problems, params)
        return problems

    def validate_inputs(self, inputs):
        problems = _counts_problems("banked", inputs.get("banked"))
        family = inputs.get("family")
        if not isinstance(family, (list, tuple)):
            problems.append(
                f"family must be a list of eligible instruments, got {family!r}"
            )
        extents = inputs.get("extents")
        if extents is not None and not isinstance(extents, dict):
            problems.append(
                f"extents must be a dict ({{instrument: {{first_ms, last_ms}}}}) "
                f"when wired, got {extents!r}"
            )
        return problems

    def run(self, ctx, inputs):
        bar = self.params["min_events"]
        banked = inputs["banked"]
        family = set(inputs["family"])
        extents = inputs.get("extents") or {}
        instruments = {}
        for instrument in sorted(set(banked) | family):
            n = banked.get(instrument, 0)
            row = {
                "banked": n,
                "in_family": instrument in family,
                "gap": max(0, bar - n),
            }
            extent = extents.get(instrument)
            if isinstance(extent, dict):
                row["first_ms"] = extent.get("first_ms")
                row["last_ms"] = extent.get("last_ms")
            instruments[instrument] = row
        in_family = sum(1 for row in instruments.values() if row["in_family"])
        pending = len(instruments) - in_family
        payload = {
            "min_events": bar,
            "instruments": instruments,
            "totals": {
                "instruments": len(instruments),
                "in_family": in_family,
                "pending": pending,
                "banked_events": sum(banked.values()),
            },
        }
        path = self.write_artifact(ctx, "banking.json", payload)
        self.log.info(
            "banking report: %d in / %d pending -> %s", in_family, pending, path
        )
        return {"path": path, "summary": {"in": in_family, "pending": pending}}


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
    """A schema rendered for a refusal message."""
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
    """``value`` as a tuple of field names, or ``None`` when it is not a
    field name / non-empty list of them. One reader for ``key`` and
    ``schema`` so the two knobs cannot drift apart."""
    names = (value,) if isinstance(value, str) else value
    if not isinstance(names, (list, tuple)) or not names:
        return None
    if any(not isinstance(name, str) or not name for name in names):
        return None
    return tuple(names)


def _declared_tables_problems(problems, params, *, values_must_be):
    """Shape problems with a ``tables`` block — ports supplied by
    DECLARATION rather than by wire.

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
    """Refuse a row a projection cannot lawfully rewrite — returns nothing,
    raises naming the node.

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
    """Union N inputs into one (role ``transform``) — the toolkit's
    ``concat`` kind, and the vocabulary two venues need to share ONE
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

    Params
    ------
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
    """

    role = "transform"
    outputs = ("merged", "sources")

    #: The class's own knobs — anything else is refused by name.
    _PARAMS = (
        "allow_empty",
        "allow_overlap",
        "key",
        "provenance",
        "provenance_waiver",
        "schema",
        "shape",
        "tables",
    )

    @classmethod
    def validate_params(cls, params):
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        _bool_problems(problems, params, ("allow_empty", "allow_overlap"))
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
        """Container shapes only — never a walk.

        Walking here would consume a one-shot stream and hand ``run`` an
        exhausted iterator (F-220 #6), so a generator is refused BY NAME
        and every row-level check waits for ``run``.
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
        """``[(identity, row), ...]`` for one port — identity is the
        mapping key in ``"table"`` shape and ``None`` in ``"records"``."""
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
        """The reference schema after checking ``row`` against it — the
        first row sets it when the document declared none."""
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
        shape = self.params["shape"]
        ports = dict(inputs)
        ports.update(self.params.get("tables") or {})
        allow_empty = self.params.get("allow_empty", False)
        allow_overlap = self.params.get("allow_overlap", False)
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
    """Align a record stream against N side tables on a declared key (role
    ``transform``) — the toolkit's ``join`` kind.

    A side table is any lookup a row needs and does not carry: a fee
    schedule, a settlement ledger, a category map. The stream arrives on
    the RESERVED port ``records``; every other port — wired, or declared
    under ``params.tables`` — is a side table, and its name is the field
    it contributes when its values are scalars.

    Rows must be MAPPINGS. A join adds fields, and a frozen venue envelope
    has nowhere to put one (see :func:`_mapping_row`); the refusal names
    the alternatives rather than quietly producing a different row type.

    Params
    ------
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
    """

    role = "transform"
    outputs = ("records", "matched")

    #: The port carrying the stream; every other port is a side table.
    _LEFT = "records"

    #: The class's own knobs — anything else is refused by name.
    _PARAMS = ("allow_fanout", "how", "key", "tables", "unmatched_fill")

    @classmethod
    def validate_params(cls, params):
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
        """Container shapes only — the stream is never walked here."""
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
        """One match rendered as the fields it contributes: a mapping
        contributes its own pairs, anything else the port's name."""
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
    """Add one field by a declared conditional (role ``transform``) — the
    toolkit's ``derive`` kind, and the only FAIL-CLOSED projection.

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
    """

    role = "transform"
    outputs = ("records", "branches")

    #: The class's own knobs — anything else is refused by name.
    _PARAMS = ("cases", "field", "overwrite")

    @classmethod
    def validate_params(cls, params):
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
                problems.extend(_clause_problems(f"cases[{i}].when[{j}]", clause))
            if not when and i != len(cases) - 1:
                problems.append(
                    f"cases[{i}] has an empty 'when', so it matches EVERY row, "
                    f"but {len(cases) - 1 - i} case(s) follow it and can never "
                    "fire — a catch-all must be the LAST case, or it is a "
                    "default in disguise"
                )
        return problems

    def validate_inputs(self, inputs):
        """Container shape only — the stream is never walked here."""
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
        """The refusal for a row no case claimed, showing what was tested."""
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
                if all(_clause_holds(row, clause) for clause in case["when"]):
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
# registration
# ---------------------------------------------------------------------------

#: The kinds this module ships, in registration order — the four
#: single-stream verbs, then the three relational ones.
_KINDS = (
    ("filter", Filter),
    ("event-bank", EventBank),
    ("eligibility", Eligibility),
    ("banking-report", BankingReport),
    ("concat", Concat),
    ("join", Join),
    ("derive", Derive),
)


def register(registry=None):
    """Register the seven flow kinds into ``registry`` (default
    :data:`~dskit.pipeline.node.DEFAULT_NODE_KINDS`), ``owned=False``.

    Idempotent by SKIPPING any name already present — never shadowing an
    existing registration (deliberate re-binding goes through the
    registry itself, which refuses duplicates loudly). Returns the
    registry for chaining. Nothing registers at import time; calling
    this is the explicit opt-in.
    """
    registry = DEFAULT_NODE_KINDS if registry is None else registry
    for name, cls in _KINDS:
        if name not in registry:
            registry.register(name, cls, owned=False)
    return registry
