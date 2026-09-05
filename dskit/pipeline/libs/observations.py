"""The ``observations`` data kind — the pipeline-facing half of the read seam (ADR-0077).

ADR-0037 landed ``scan_stream`` / ``stream_digest`` as functions on the
onboarding package; every child that read an acquired stream then wrote
the same wrapper around them — the root/source/stream knobs, a memoized
scan so resolve and execute see ONE snapshot, a content digest as the
fingerprint, a timestamp flattened to ``asof_ms``. This pack is that
wrapper, once. A child SUBCLASSES it to fix its vocabulary (the key
fields its stream is deduplicated on are a fact about the stream, so a
subclass pins them with :func:`~dskit.pipeline.libs.numpy.narrow_params`
style narrowing rather than leaving them a free knob) and to PROJECT the
raw rows into its record envelope by overriding :meth:`project`.

Why a tier-2 pack and not core: the pipeline core is stdlib-only and
must not import its sibling package at module level (the purity gate);
``scan_stream`` is therefore imported inside the scan, exactly as a node
pack names its library inside ``run()``.

Import cost: stdlib + the pipeline core.
"""

from __future__ import annotations

import math

from dskit.pipeline.node import (
    DEFAULT_NODE_KINDS,
    Node,
    ServingContract,
    check_int_param,
    reject_unknown_params,
)
from dskit.pipeline.records import ASOF_FIELD

__all__ = [
    "DEFAULT_TS_OUT",
    "DEFAULT_TS_UNIT",
    "DIGEST_RECIPE_KIND",
    "NODE_KINDS",
    "SOURCE_BINDING_KIND",
    "TS_UNITS",
    "ObservationRows",
    "register",
]

#: The units a ``ts_field`` may be read in. ``iso`` is an ISO-8601 stamp
#: (the onboarding convention, naive = UTC); ``ms`` an integer epoch
#: millisecond already, copied through unchanged.
TS_UNITS = ("iso", "ms")

#: The ONE name for each default, read by ``validate_params`` and by the
#: scan alike so the gate can never approve a value the run ignores. The
#: stamp field is the envelope's own decision-instant name
#: (:data:`~dskit.pipeline.records.ASOF_FIELD`) — imported, not respelled,
#: so what this kind writes is what the split policies cut on.
DEFAULT_TS_UNIT = "iso"
DEFAULT_TS_OUT = ASOF_FIELD

#: The two ``kind`` spellings the kind's serving contract carries
#: (ADR-0091): what its ``source_binding`` binds — an onboarding stream —
#: and the recipe a served snapshot is digested by. Named once, here, so
#: the serving side imports the spelling rather than restating it.
SOURCE_BINDING_KIND = "onboarding-stream"
DIGEST_RECIPE_KIND = "stream-digest"


def _ok_name(value):
    """Say whether ``value`` is a non-empty string."""
    return isinstance(value, str) and bool(value)


class ObservationRows(Node):
    """Emit one deduplicated snapshot of an acquired observation stream.

    Role ``data`` — the ``observations`` kind. The scan runs ONCE per
    instance: ``fingerprint()`` (at resolve) and ``run()`` (at execute)
    read the same memoized snapshot, so a live puller appending
    acquisitions underneath cannot hand the run rows its identity never
    hashed.

    Parameters
    ----------
    params : dict
        ``root`` (str, REQUIRED) — the onboarding root; ``source`` (str,
        REQUIRED) — the registered source name; ``stream`` (str,
        REQUIRED) — the stream; ``key_fields`` (non-empty list of str,
        REQUIRED) — the ``data`` fields the stream is deduplicated on
        (latest ``acquired_at`` wins per key); ``ts_field`` (str,
        optional) — a ``data`` field holding each row's instant;
        ``ts_unit`` (``"iso"`` | ``"ms"``, default ``"iso"``) — how to
        read it; ``ts_out`` (str, default ``"asof_ms"``) — the epoch-ms
        field the instant is written to, in place (a record already
        carrying it refuses, under either unit); ``shared_fields``
        (list of str, default ``[]``) — heavily repeated values to intern;
        ``since_ms`` (int >= 0, optional) — keep only rows at or after
        this instant (needs ``ts_field``). A document that will be SERVED
        declares it as ``null``: the serving window is an override, and an
        override may only address a param that already exists (ADR-0091).

    Examples
    --------
    Read a bar stream back, one row per ``(symbol, ts)``::

        node = ObservationRows("bars", {
            "root": "./ob", "source": "alpaca", "stream": "bars",
            "key_fields": ["symbol", "ts"], "ts_field": "ts",
        })
        node.fingerprint()["rows"]        # how many rows the run will see
        out = node.run(ctx, {})
        # -> {"records": [{"symbol": ..., "ts": ..., "asof_ms": ...}, ...]}
    """

    role = "data"
    outputs = ("records",)

    _PARAMS = (
        "root",
        "source",
        "stream",
        "key_fields",
        "ts_field",
        "ts_unit",
        "ts_out",
        "shared_fields",
        "since_ms",
    )

    #: The memoized snapshot — set per INSTANCE on first read (a class-level
    #: cache would blind every later node to new data).
    _snap = None

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
            One problem per unknown knob, missing required knob, or
            unusable value.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        for name in ("root", "source", "stream"):
            if name in cls._PARAMS and not _ok_name(params.get(name)):
                problems.append(
                    f"{name} is required and must be a non-empty string, "
                    f"got {params.get(name)!r}"
                )
        if "key_fields" in cls._PARAMS:
            keys = params.get("key_fields")
            if (
                not isinstance(keys, (list, tuple))
                or not keys
                or any(not _ok_name(k) for k in keys)
            ):
                problems.append(
                    "key_fields is required — the dedup key is a fact about "
                    f"the stream, stated as a non-empty list of field names, got {keys!r}"
                )
        if "ts_field" in cls._PARAMS:
            ts_field = params.get("ts_field")
            if ts_field is not None and not _ok_name(ts_field):
                problems.append(f"ts_field must be a non-empty string, got {ts_field!r}")
        if "ts_unit" in cls._PARAMS:
            unit = params.get("ts_unit", DEFAULT_TS_UNIT)
            if unit not in TS_UNITS:
                problems.append(f"ts_unit must be one of {list(TS_UNITS)}, got {unit!r}")
        if "ts_out" in cls._PARAMS and not _ok_name(params.get("ts_out", DEFAULT_TS_OUT)):
            problems.append(f"ts_out must be a non-empty string, got {params.get('ts_out')!r}")
        if "shared_fields" in cls._PARAMS:
            shared = params.get("shared_fields", ())
            if not isinstance(shared, (list, tuple)) or any(not _ok_name(f) for f in shared):
                problems.append(
                    f"shared_fields must be a list of field-name strings, got {shared!r}"
                )
        if "since_ms" in cls._PARAMS and params.get("since_ms") is not None:
            check_int_param(problems, "since_ms", params.get("since_ms"), ge=0)
            if not params.get("ts_field") and "ts_field" in cls._PARAMS:
                problems.append("since_ms needs a ts_field to compare against")
        return problems

    # -- the serving classification (ADR-0091) ------------------------------

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Classify the kind for serving: it IS the tick's one mutable read.

        Parameters
        ----------
        params : dict
            The declared params; unused — every observation read is the
            entry's, whatever it declares.
        verified_run_evidence : dict
            The release's evidence; unused — no evidence makes a stream
            read pure.

        Returns
        -------
        str
            ``"entry_read"``, always.
        """
        return "entry_read"

    @classmethod
    def serving_contract(cls, params, verified_run_evidence):
        """Declare how a serving loop snapshots this stream — pure, document-blind.

        Four facts, all read from the declared params and nothing else:
        the source binding (``root``/``source``/``stream``), the ENTITY
        projection (``key_fields`` with ``ts_field`` projected out — the
        dedupe key may contain time, and an entity may not), the
        event-time field rows carry (``ts_out``) and the digest recipe
        (``key_fields``/``ts_field``/``ts_unit``, verbatim). No universe:
        the serve document owns that, and a classmethod could only have
        guessed it from the dedupe key.

        Parameters
        ----------
        params : dict
            The node's declared params, as the document states them.
        verified_run_evidence : dict
            Unused — the contract is a fact about the declared stream.

        Returns
        -------
        ServingContract
            The declaration a serving loop snapshots and digests by.

        Raises
        ------
        ValueError
            When ``ts_field`` is absent — no instant, no watermark, cannot
            serve — or when projecting it out leaves no entity key.
        """
        ts_field = params.get("ts_field")
        if not _ok_name(ts_field):
            raise ValueError(
                "observations: serving needs a ts_field — without an event "
                "instant no watermark can be drawn, so the stream cannot be "
                f"the entry (got {ts_field!r})"
            )
        key_fields = list(params.get("key_fields") or ())
        entity = tuple(f for f in key_fields if f != ts_field)
        if not entity:
            raise ValueError(
                f"observations: key_fields {key_fields!r} name nothing but the "
                "instant — projecting the time field out leaves no entity key, "
                "and a served snapshot must identify entities across ticks"
            )
        return ServingContract(
            source_binding={
                "kind": SOURCE_BINDING_KIND,
                "root": params.get("root"),
                "source": params.get("source"),
                "stream": params.get("stream"),
            },
            entity_key_fields=entity,
            event_time_field=params.get("ts_out", DEFAULT_TS_OUT),
            digest_recipe={
                "kind": DIGEST_RECIPE_KIND,
                "key_fields": key_fields,
                "ts_field": ts_field,
                "ts_unit": params.get("ts_unit", DEFAULT_TS_UNIT),
            },
        )

    # -- the vocabulary hooks a subclass fixes -----------------------------

    def root(self):
        """Name the onboarding root the stream is read from (str)."""
        return self.params["root"]

    def source(self):
        """Name the registered source the stream belongs to (str)."""
        return self.params["source"]

    def stream(self):
        """Name the stream read — a subclass pins its own default here (str)."""
        return self.params["stream"]

    def key_fields(self):
        """Name the ``data`` fields the stream is deduplicated on (tuple)."""
        return tuple(self.params["key_fields"])

    def ts_field(self):
        """Name the ``data`` field carrying each row's instant, or ``None``."""
        return self.params.get("ts_field")

    def ts_unit(self):
        """Say how ``ts_field`` is read — one of :data:`TS_UNITS` (str)."""
        return self.params.get("ts_unit", DEFAULT_TS_UNIT)

    def ts_out(self):
        """Name the epoch-ms field the instant lands in (str)."""
        return self.params.get("ts_out", DEFAULT_TS_OUT)

    def shared_fields(self):
        """Name the heavily repeated fields to intern (tuple)."""
        return tuple(self.params.get("shared_fields", ()))

    def since_ms(self):
        """Give the inclusive lower bound on the instant, or ``None``."""
        return self.params.get("since_ms")

    def project(self, records):
        """Turn the deduplicated raw rows into this kind's records.

        The base emits the rows as they are. A child overrides this to
        build its envelope (a :class:`~dskit.pipeline.records.MarketRecord`
        per row, say); the memoized snapshot is the PROJECTED list, so the
        projection runs once per instance too.

        Parameters
        ----------
        records : list of dict
            The winning ``data`` dicts, instant flattened to ``ts_out``.

        Returns
        -------
        list
            The records this kind emits.
        """
        return records

    # -- the scan ----------------------------------------------------------

    def _scan(self):
        """Read the stream once and memoize the projection."""
        if self._snap is not None:
            return self._snap
        # Imported HERE, not at module top: the pipeline core is stdlib-only
        # and the purity gate refuses a module-level import of the sibling
        # package — the same rule a node pack keeps for its library.
        from dskit.onboarding.observations import scan_stream

        ts_field = self.ts_field()
        unit = self.ts_unit()
        # The accessors are the subclass seam, so the plan-time gate never
        # saw their answers: an off-vocabulary unit must not ride the
        # ``ms`` branch by default, and a bound with nothing to bound must
        # not vanish (the ``iso`` seam refuses it; the ``ms`` path did not).
        if unit not in TS_UNITS:
            raise ValueError(
                f"{self.key}: ts_unit() must answer one of {list(TS_UNITS)}, "
                f"got {unit!r}"
            )
        if ts_field is None and self.since_ms() is not None:
            raise ValueError(
                f"{self.key}: since_ms={self.since_ms()!r} bounds an instant, "
                "but ts_field() names no field to read it from"
            )
        records = scan_stream(
            self.root(),
            self.source(),
            self.stream(),
            key_fields=self.key_fields(),
            ts_field=ts_field if unit == "iso" else None,
            ts_out=self.ts_out(),
            shared_fields=self.shared_fields(),
            since_ms=self.since_ms() if unit == "iso" else None,
        )
        if ts_field is not None and unit == "ms":
            records = self._stamp_ms(records, ts_field)
        self._digest = self._digest_of(records)
        self._snap = self.project(records)
        return self._snap

    def _stamp_ms(self, records, ts_field):
        """Copy an epoch-ms field onto ``ts_out`` in place, honoring ``since_ms``."""
        out_name = self.ts_out()
        floor = self.since_ms()
        kept = []
        for record in records:
            # The seam's own rule under ``iso`` — a record already carrying
            # ``ts_out`` refuses, never a silent clobber — holds here too;
            # naming the carried field AS ``ts_field`` is the lawful spelling.
            if out_name != ts_field and out_name in record:
                raise ValueError(
                    f"{self.key}: data already carries {out_name!r} (ts_out) — "
                    f"refusing to overwrite it under ts_unit='ms'; declare "
                    f"ts_field={out_name!r} to read it as the instant"
                )
            value = record.get(ts_field)
            # "Copied through unchanged" means an INTEGER count: a float
            # spelling of one (``1.7e12``) is exact and accepted, while a
            # fractional, NaN or infinite value refuses by name — ``int()``
            # would truncate the first silently and raise a nameless
            # ValueError / OverflowError on the other two.
            integral = isinstance(value, int) or (
                isinstance(value, float) and math.isfinite(value) and value.is_integer()
            )
            if isinstance(value, bool) or not integral:
                raise ValueError(
                    f"{self.key}: {ts_field!r} must be an integer epoch-ms count "
                    f"under ts_unit='ms', got {value!r}"
                )
            stamp = int(value)
            if floor is not None and stamp < floor:
                continue
            record[out_name] = stamp
            kept.append(record)
        return kept

    @staticmethod
    def _digest_of(records):
        """Compute the frozen dump digest, before projection."""
        from dskit.onboarding.observations import stream_digest

        return stream_digest(records)

    def fingerprint(self):
        """Answer the stream's content-derived identity.

        Returns
        -------
        dict
            ``{"kind", "rows", "sha256"}`` — JSON-small; it moves whenever
            any row a run would consume changes.
        """
        records = self._scan()
        return {"kind": type(self).__name__, "rows": len(records), "sha256": self._digest}

    def data_edge(self):
        """Give the newest instant the stream reaches, or ``None``.

        Returns
        -------
        int or None
            The maximum ``ts_out`` over the emitted records when a
            ``ts_field`` is declared; ``None`` otherwise.
        """
        if self.ts_field() is None:
            return None
        name = self.ts_out()
        edge = None
        for record in self._scan():
            stamp = record.get(name) if isinstance(record, dict) else getattr(record, name, None)
            if isinstance(stamp, (int, float)) and not isinstance(stamp, bool):
                edge = int(stamp) if edge is None else max(edge, int(stamp))
        return edge

    def run(self, ctx, inputs):
        """Emit the memoized snapshot.

        Parameters
        ----------
        ctx : NodeContext
            The run frame; unused — a source reads only its params.
        inputs : dict
            Empty: role ``data`` takes no inputs.

        Returns
        -------
        dict
            ``{"records": [...]}``.
        """
        records = self._scan()
        self.log.info("emitting %d observation record(s)", len(records))
        return {"records": records}


#: The pack's kinds.
NODE_KINDS = (("observations", ObservationRows),)


def register(registry=None) -> None:
    """Claim the pack's kind names in ``registry`` (default the toolkit's).

    Parameters
    ----------
    registry : NodeKindRegistry or None
        Where to register; ``None`` means
        :data:`~dskit.pipeline.node.DEFAULT_NODE_KINDS`. Idempotent — a
        name already present is skipped, never shadowed.

    Returns
    -------
    None
        Registration is the effect.
    """
    registry = DEFAULT_NODE_KINDS if registry is None else registry
    for name, cls in NODE_KINDS:
        if name not in registry:
            registry.register(name, cls)
