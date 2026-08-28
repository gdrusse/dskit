"""The banking/admission chain: ``event-bank``, ``eligibility``, ``banking-report``.

Spec §10 step 3. Three kinds, one story: the ★BANKING counter accrues evidence per
instrument, the admission gate decides which instruments have enough of
it to be tested at all, and the ledger writes down who is in, who is
pending and how far each still has to go. That
``bank -> family -> report`` spine is why the three live together rather
than beside the record-flow verbs they were originally written next to
(TODO 3e). To read THESE THREE wired end to end under the driver, see
``flow_document`` in ``tests/pipeline/test_kinds_flow.py`` and the
integration case that runs it — a document naming ``event-bank`` ->
``eligibility`` -> ``banking-report`` by kind. (The synthetic
``demo_pipeline`` sketches the same SHAPE, but with the stand-in kinds
``synth-bank``/``synth-eligibility``/``synth-report``, whose ports
differ; it is not these classes.)

They are registered with ``owned=False`` — any project may shadow them
with its own class via an import path; the doctrine kinds (``stat_test``,
``validate``) are a separate, owned matter.

The bar is the point. ``min_events`` has NO default in either the gate or
the ledger: an admission bar that was assumed rather than stated is a bar
nobody chose, and this gate decides which markets get tested at all. For
the same reason the counter's ``distinct_by`` defaults to the option that
CANNOT overstate evidence.

Record tolerance follows the ``kinds_flow`` convention: records are plain
dicts or objects with attributes (e.g.
:class:`~dskit.pipeline.records.MarketRecord`), every field read goes
through the single :func:`~dskit.pipeline.kinds_flow._field` accessor
imported from there, and a record lacking a needed field is SKIPPED,
never crashed on — sparse records are data, not shape errors.

Validator convention: params may legally arrive as unmaterialized
``$``-references at plan time (``"$splits.train_end_ms"`` is the designed
use for ``strictly_before``); validators tolerate the ``$``-form and
check the real value when construction sees the materialized params.

No import-time side effects: nothing registers until :func:`register` is
called.

Import cost: stdlib only.
"""

from __future__ import annotations

from dskit.pipeline.document import is_node_ref
from dskit.pipeline.kinds_flow import _MISSING, _field
from dskit.pipeline.kinds_stats import _reject_unknown
from dskit.pipeline.node import DEFAULT_NODE_KINDS, Node

__all__ = [
    "BankingReport",
    "Eligibility",
    "EventBank",
    "register",
]

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

#: The ONE name for ``event-bank``'s ``distinct_by`` default.
#:
#: The vocabulary above says what may be counted; this says what is counted
#: when nobody chose. It is read by ``validate_params`` (including its
#: refusal message), by ``validate_inputs`` and by ``run`` — a default
#: restated as a literal in each of those is the copy that drifts.
_DEFAULT_DISTINCT_BY = "group"

#: The ONE name for ``event-bank``'s ``count`` default.
#:
#: ``"settled"`` counts only events whose contract is present in the
#: ``outcomes`` input, which is why the default also decides whether that
#: port is REQUIRED: ``validate_inputs`` demands it and ``run`` reads it,
#: so the two must read the same name or the gate guards a port ``run``
#: does not use (or misses one it does).
_DEFAULT_COUNT = "settled"


def _require_min_events(problems, params) -> None:
    """Append the ``min_events`` problems: REQUIRED, no default, int >= 1."""
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
    """Problems with a banked-counts input: ``{instrument: int >= 0}``."""
    if not isinstance(counts, dict):
        return [f"{port} must be a counts dict ({{instrument: n}}), got {counts!r}"]
    return [
        f"{port}[{instrument!r}] must be an int >= 0, got {n!r}"
        for instrument, n in counts.items()
        if isinstance(n, bool) or not isinstance(n, int) or n < 0
    ]


# ---------------------------------------------------------------------------
# event-bank — the ★BANKING counter (role: accrual)
# ---------------------------------------------------------------------------


class EventBank(Node):
    """Count banked events per instrument — the ``event-bank`` kind.

    ★BANKING, role ``accrual``: the week-over-week
    ``12 -> 27 -> 43 -> 51`` counter the admission gate reads.

    Sparse-record semantics: an event missing ``instrument`` or
    ``contract``, or whose ``asof_ms`` is not an int, cannot prove where
    (or when) it banks and is SKIPPED, never crashed on; skips are logged.

    Parameters
    ----------
    params : dict
        ``count`` (str, default :data:`_DEFAULT_COUNT`) — ``"settled"``
        counts only events whose contract is present in ``outcomes`` (which
        the input contract therefore requires at execute), ``"all"`` counts
        every surviving event. ``distinct_by`` (str, default
        :data:`_DEFAULT_DISTINCT_BY`, one of
        :data:`_DISTINCT_FIELDS`) — WHAT one "event" is in the stream you
        wired in. A market ladder carries every lead time of every strike
        contract, so counting records reports a multiple of the truth and
        a ``min_events`` gate opens on a fraction of the evidence it
        names; the default is the one that cannot overstate.
        ``strictly_before`` (int epoch-ms or ``None``, default ``None``) —
        the knowable-at-T1 cut, arriving pre-materialized (the designed
        wiring is ``"$splits.train_end_ms"``-style references): only
        events STRICTLY below it count.

    Examples
    --------
    Bank one event per dependence cluster, settled only, before the
    training cut::

        node = EventBank(
            "bank",
            {"count": "settled", "distinct_by": "group",
             "strictly_before": 1735689600000},
        )
        out = node.run(ctx, {"events": events, "outcomes": outcomes})
    """

    role = "accrual"
    outputs = ("counts", "extents")

    #: The class's own knobs — anything else is refused by name.
    _PARAMS = ("count", "distinct_by", "strictly_before")

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
        count = params.get("count", _DEFAULT_COUNT)
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
                f"absent, defaulting to {_DEFAULT_DISTINCT_BY!r} — one event "
                f"counted once), got {distinct_by!r}"
            )
        return problems

    def validate_inputs(self, inputs):
        """Problems with the materialized inputs, empty when none.

        Parameters
        ----------
        inputs : dict
            ``events`` (list of records carrying ``instrument``,
            ``contract``, ``asof_ms``) and, under ``count="settled"``,
            ``outcomes`` (dict ``{contract: settled-YES?}``, whose KEYS
            mark settledness — a settled NO is settled).

        Returns
        -------
        list of str
            One message per problem; empty when the inputs are usable.
        """
        problems = []
        if not isinstance(inputs.get("events"), list):
            problems.append(
                f"events must be a list of records, got {inputs.get('events')!r}"
            )
        if self.params.get("count", _DEFAULT_COUNT) == "settled" and not isinstance(
            inputs.get("outcomes"), dict
        ):
            problems.append(
                "count='settled' requires the outcomes input "
                f"({{contract: settled-YES?}}) to know which events are "
                f"settled, got {inputs.get('outcomes')!r}"
            )
        return problems

    def run(self, ctx, inputs):
        """Bank the events and report the counts and their data extents.

        Parameters
        ----------
        ctx : NodeContext
            The run frame; unused here beyond the node's own logging.
        inputs : dict
            As validated by :meth:`validate_inputs`.

        Returns
        -------
        dict
            ``counts`` — ``{instrument: n}`` DISTINCT events (see
            ``distinct_by``); ``extents`` — ``{instrument: {"first_ms",
            "last_ms"}}`` over every OBSERVATION that fed those counts,
            not over the first sighting of each event, so the extent stays
            the honest span of the banked data.
        """
        count_settled = self.params.get("count", _DEFAULT_COUNT) == "settled"
        cut = self.params.get("strictly_before")
        distinct_by = self.params.get("distinct_by", _DEFAULT_DISTINCT_BY)
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
    """Admit the instruments that cleared the bar — the ``eligibility`` kind.

    Role ``gate``: instruments whose banked count clears ``min_events``
    form the family; an empty family is a NO-GO, and THAT is this node's
    point — the driver halts every DAG descendant on the verdict, so
    nothing downstream ever runs on an inadmissible universe.

    Parameters
    ----------
    params : dict
        ``min_events`` (int >= 1, REQUIRED, no default) — the admission
        bar must be stated, never assumed.

    Examples
    --------
    Admit every instrument with at least fifty banked events::

        node = Eligibility("eligible_family", {"min_events": 50})
        out = node.run(ctx, {"banked": {"MARKET-A": 51, "MARKET-B": 43}})
    """

    role = "gate"
    outputs = ("instruments", "verdict")

    #: The class's own knobs — anything else is refused by name.
    _PARAMS = ("min_events",)

    @classmethod
    def validate_params(cls, params):
        """Problems with this node's declared knobs, empty when none.

        Parameters
        ----------
        params : dict
            The node's ``params`` block.

        Returns
        -------
        list of str
            One message per problem; empty when the params are legal.
        """
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        _require_min_events(problems, params)
        return problems

    def validate_inputs(self, inputs):
        """Problems with the materialized inputs, empty when none.

        Parameters
        ----------
        inputs : dict
            ``banked`` — the ``event-bank`` counts dict.

        Returns
        -------
        list of str
            One message per problem; empty when the inputs are usable.
        """
        return _counts_problems("banked", inputs.get("banked"))

    def run(self, ctx, inputs):
        """Apply the bar and return the family with its GO / NO-GO verdict.

        Parameters
        ----------
        ctx : NodeContext
            The run frame; unused here beyond the node's own logging.
        inputs : dict
            As validated by :meth:`validate_inputs`.

        Returns
        -------
        dict
            ``instruments`` — the sorted family (list of str);
            ``verdict`` — ``"GO"`` iff the family is non-empty, else
            ``"NO-GO"``.
        """
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
    """Write the banking ledger — the ``banking-report`` kind.

    Role ``report``: who is IN the family, who is pending at 43/50, and
    how far each has to go.

    Writes ``banking.json`` into this node's artifact dir: per instrument
    ``{banked, in_family, gap}`` with ``gap = max(0, min_events - n)``
    (plus ``first_ms``/``last_ms`` when extents are wired), and totals.
    Rows cover the union of banked and family instruments, so a family
    member with no counts row still appears (banked 0) instead of
    vanishing from the ledger.

    Parameters
    ----------
    params : dict
        ``min_events`` (int >= 1, REQUIRED, no default) — the same stated
        bar the gate applied.

    Examples
    --------
    Write the ledger for the same bar the gate used::

        node = BankingReport("banking_report", {"min_events": 50})
        out = node.run(
            ctx, {"banked": {"MARKET-A": 51}, "family": ["MARKET-A"]}
        )
    """

    role = "report"
    outputs = ("path", "summary")

    #: The class's own knobs — anything else is refused by name.
    _PARAMS = ("min_events",)

    @classmethod
    def validate_params(cls, params):
        """Problems with this node's declared knobs, empty when none.

        Parameters
        ----------
        params : dict
            The node's ``params`` block.

        Returns
        -------
        list of str
            One message per problem; empty when the params are legal.
        """
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        _require_min_events(problems, params)
        return problems

    def validate_inputs(self, inputs):
        """Problems with the materialized inputs, empty when none.

        Parameters
        ----------
        inputs : dict
            ``banked`` (the counts dict), ``family`` (the eligible
            instruments, list or tuple), and OPTIONAL ``extents``
            (``{instrument: {first_ms, last_ms}}``).

        Returns
        -------
        list of str
            One message per problem; empty when the inputs are usable.
        """
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
        """Write ``banking.json`` and return its path with the headline split.

        Parameters
        ----------
        ctx : NodeContext
            The run frame — supplies the artifact dir written into.
        inputs : dict
            As validated by :meth:`validate_inputs`.

        Returns
        -------
        dict
            ``path`` — the artifact's path (str); ``summary`` —
            ``{"in": k, "pending": m}``.
        """
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

#: The kinds this module ships, in registration order — the accrual, the
#: gate it feeds, and the ledger that reads both.
_KINDS = (
    ("event-bank", EventBank),
    ("eligibility", Eligibility),
    ("banking-report", BankingReport),
)


def register(registry=None):
    """Register the three banking kinds, ``owned=False``.

    Idempotent by SKIPPING any name already present — never shadowing an
    existing registration (deliberate re-binding goes through the registry
    itself, which refuses duplicates loudly). Nothing registers at import
    time; calling this is the explicit opt-in.

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
