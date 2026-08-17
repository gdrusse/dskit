"""The ordinary flow kinds — ``filter``, ``event-bank``, ``eligibility``,
``banking-report`` (spec §10 step 3).

These are the toolkit's plain, un-owned kinds: the record filter, the
★BANKING counter, the admission gate, and the banking ledger — the
``bank -> eligible_family -> banking_report`` spine of
``examples/pipeline/PROPOSAL-node-map.jsonc``. They are registered with
``owned=False`` (any project may shadow them with its own class via an
import path); the doctrine kinds (``stat_test``, ``validate``) are a
separate, owned matter.

Record tolerance — one rule, everywhere: records flowing through these
kinds are either plain dicts or objects with attributes (e.g.
:class:`~dskit.pipeline.records.MarketRecord`), and every field access
goes through the single :func:`_field` accessor. A record that lacks a
needed field is DROPPED or SKIPPED, never crashed on — sparse records
are data, not shape errors (each class documents its exact semantics).

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

from dskit.pipeline.document import is_node_ref
from dskit.pipeline.kinds_stats import _reject_unknown
from dskit.pipeline.node import DEFAULT_NODE_KINDS, Node

__all__ = [
    "BankingReport",
    "Eligibility",
    "EventBank",
    "Filter",
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
# registration
# ---------------------------------------------------------------------------

#: The kinds this module ships, in registration order.
_KINDS = (
    ("filter", Filter),
    ("event-bank", EventBank),
    ("eligibility", Eligibility),
    ("banking-report", BankingReport),
)


def register(registry=None):
    """Register the four flow kinds into ``registry`` (default
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
