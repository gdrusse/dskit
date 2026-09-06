"""``nodes`` — the child's pipeline seam: registered Node kinds.

Two kinds show the whole shape a real child repeats: a ``data`` source
(:class:`SampleRecords`) and a ``transform`` (:class:`EnrichRecords`),
both with default-deny params (a typo'd knob is an error, never a silent
default — the toolkit's config doctrine). Importing this module IS the
registration: the kinds land in the toolkit registry under the
``yourproject-`` prefix, which is how ``--adapter yourproject`` makes a
document's ``"uses": "yourproject-sample"`` resolve. ``owned`` is never
set — ownership is toolkit doctrine, not a child's to claim.

The data node fronts an in-memory table so the skeleton runs anywhere; a
real child scans its store (files, a database, an API dump) in the same
shape: content-derived ``fingerprint()``, and ONE scan memoized on the
instance so identity (taken at resolve) and execution (run later) see the
same snapshot even while a live writer appends underneath.

Import cost: stdlib + dskit — a document naming these kinds must PLAN on
machines with nothing heavy installed, so heavy imports live inside
``run()`` (see :class:`EnrichRecords`), never at module top.
"""

from __future__ import annotations

import hashlib
import json
import math

from dskit.pipeline.node import (
    Node,
    ServingContract,
    register_node_kind,
    reject_unknown_params,
)

__all__ = ["EnrichRecords", "NODE_KINDS", "SAMPLE_ROWS", "SampleRecords"]

#: The in-memory "store" the data node fronts — the stand-in for a real
#: child's files/database. Mutable on purpose: the conformance suite
#: rewrites and appends to it to prove the fingerprint tracks CONTENT.
SAMPLE_ROWS = [
    {"id": "sample-0001", "day": "2026-01-01", "value": 10.0},
    {"id": "sample-0002", "day": "2026-01-02", "value": 11.5},
    {"id": "sample-0003", "day": "2026-01-03", "value": 9.75},
]


#: Default-deny on this class's own knobs. IMPORTED, never copied: the
#: toolkit exports this helper precisely so a child does not carry its own
#: drifting duplicate. Same for ``check_int_param`` when you need it.
_reject_unknown = reject_unknown_params


class SampleRecords(Node):
    """Emit the sample table's records (role ``data`` — a source: no
    inputs, fully literal params) — the ``yourproject-sample`` kind.

    Params: ``limit`` — optional int >= 1 capping how many records are
    emitted; absent = the whole table.

    One instance is one view: the first ``fingerprint()``/``run()`` copies
    the table onto the instance, so the driver's resolve/execute straddle
    consumes exactly the snapshot its run identity hashed.
    """

    role = "data"
    outputs = ("records",)

    #: The class's own knobs — anything else is refused by name.
    _PARAMS = ("limit",)

    #: Instance scan cache (set per instance on first read — caching at
    #: CLASS level would blind every later node to new data).
    _snap = None

    @classmethod
    def validate_params(cls, params):
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        limit = params.get("limit")
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
        ):
            problems.append(f"limit must be an int >= 1, got {limit!r}")
        return problems

    def _scan(self):
        """The memoized snapshot — rows are COPIED so a writer mutating
        the store in place cannot reach back into this instance's view."""
        if self._snap is None:
            self._snap = [dict(row) for row in SAMPLE_ROWS]
        return self._snap

    def fingerprint(self):
        """Content-derived, JSON-small: moves whenever the data a run
        would consume changes — a count-only or params-echo fingerprint
        is content-blind and fails conformance."""
        rows = self._scan()
        digest = hashlib.sha256(
            json.dumps(rows, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return {"kind": "yourproject-sample", "rows": len(rows), "sha256": digest}

    def run(self, ctx, inputs):
        records = [dict(row) for row in self._scan()[: self.params.get("limit")]]
        self.log.info("emitting %d record(s)", len(records))
        return {"records": records}

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Answer ``entry_read`` — this is where live rows enter (ADR-0091).

        The serving policy classifies every node BEFORE anything is
        constructed, and the default is ``forbidden``: a class that does
        not answer here can never appear in a served graph. A source is
        the one mutable read a tick may take, so it says so.

        Parameters
        ----------
        params : dict
            The node's declared params; unread here, but a class whose
            effect depends on a knob reads it rather than guessing.
        verified_run_evidence : dict
            Release evidence (mode, artifact pinning); unread by a source.

        Returns
        -------
        str
            ``"entry_read"`` — a member of
            :data:`~dskit.pipeline.node.SERVING_EFFECTS`.
        """
        return "entry_read"

    @classmethod
    def serving_contract(cls, params, verified_run_evidence):
        """Describe the source so a tick can freeze and digest its rows.

        Pure and document-blind: it names the binding, the fields that
        identify an entity, the field carrying event time, and the recipe
        that digests a key's rows. It deliberately carries NO universe —
        the required key set is the serve document's, pinned into the
        release, because a source cannot know which keys an operator
        requires this tick.

        Parameters
        ----------
        params : dict
            The node's declared params.
        verified_run_evidence : dict
            Release evidence; unread here.

        Returns
        -------
        ServingContract
            Source binding, entity keys, event-time field, digest recipe.

        Raises
        ------
        ConfigError
            When the projection would be empty.
        """
        return ServingContract(
            source_binding={"kind": "yourproject-sample"},
            entity_key_fields=("id",),
            event_time_field="day",
            digest_recipe={"kind": "canonical-rows", "key_fields": ["id"]},
        )


class EnrichRecords(Node):
    """Derive a field per record from a required numeric param (role
    ``transform``) — the ``yourproject-enrich`` kind.

    Inputs: ``records`` — a list of record dicts. Params: ``factor``
    (REQUIRED — the bar must be stated, there is no default): each output
    record gains ``derived = value * factor``.

    Sparse-record semantics, the toolkit's rule: a record without a
    numeric ``value`` cannot be enriched and is DROPPED (and logged),
    never crashed on — sparse records are data, not shape errors.
    """

    role = "transform"
    outputs = ("records",)

    #: The class's own knobs — anything else is refused by name.
    _PARAMS = ("factor",)

    @classmethod
    def validate_params(cls, params):
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        if "factor" not in params:
            problems.append(
                "factor is required — the derivation must be stated "
                "explicitly, there is no default"
            )
            return problems
        factor = params["factor"]
        if (
            isinstance(factor, bool)
            or not isinstance(factor, (int, float))
            or not math.isfinite(factor)
        ):
            problems.append(f"factor must be a finite number, got {factor!r}")
        return problems

    def validate_inputs(self, inputs):
        # Refuse a non-list LOUDLY without walking it: this runs right
        # before run() on the SAME objects, and consuming a one-shot
        # stream here would hand run() an exhausted iterator.
        if not isinstance(inputs.get("records"), list):
            return [
                f"records must be a list of record dicts, got "
                f"{inputs.get('records')!r}"
            ]
        return []

    def run(self, ctx, inputs):
        # A HEAVY import (numpy, torch, a vendor SDK) belongs exactly
        # HERE, inside run() — never at module top, so documents naming
        # this kind still plan on machines without it installed.
        factor = self.params["factor"]
        kept = []
        for record in inputs["records"]:
            value = record.get("value") if isinstance(record, dict) else None
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue  # sparse record — cannot derive, dropped
            kept.append({**record, "derived": value * factor})
        self.log.info(
            "enriched %d/%d record(s)", len(kept), len(inputs["records"])
        )
        return {"records": kept}

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Answer ``pure`` — this transform reads nothing but its inputs.

        A served tick runs pure descendants of the entry from the frozen
        snapshot. Anything that touches a file, a socket or a store must
        NOT answer ``pure``; the fail-closed default is there so silence
        is always the safe answer.

        Parameters
        ----------
        params : dict
            The node's declared params.
        verified_run_evidence : dict
            Release evidence; unread by a pure transform.

        Returns
        -------
        str
            ``"pure"``.
        """
        return "pure"


#: kind name -> class: what the registry, the conformance suite, and a
#: document's ``uses`` all key off. Prefix kinds with the child's name —
#: the registry is shared, and a bare "sample" is a collision waiting.
NODE_KINDS = {
    "yourproject-sample": SampleRecords,
    "yourproject-enrich": EnrichRecords,
}

# Import = registration (``owned`` deliberately NOT set): the moment this
# module imports — `import yourproject`, or `--adapter yourproject` on
# the CLI — every kind above resolves by name.
for _name, _cls in NODE_KINDS.items():
    register_node_kind(_name, _cls)
