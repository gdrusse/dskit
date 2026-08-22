"""The registry — the engine, and the ONLY mutation path (ADR-0007).

One class turns a pinned model + a store into the spec's registries:
what the model declares, the registry enforces; what the store persists,
the registry stamps. Nothing else in the package writes to a store.

The layering of checks, from the inside out:

- :func:`~dskit.assets.record.check_payload` decides everything a record
  and its spec can decide alone (fields, types, required refs).
- The registry adds the checks that need the STORE: every ref must
  resolve to a present record of the declared kind, the supplied model
  must hash to the store's pin, and lifecycle moves must follow the
  model's transition map (default-deny).

Lifecycle state is never stored on a record — it is DERIVED by replaying
the append-only event log, so history is immutable and the current state
is always explainable from evidence (spec principle: immutable history).

Registration is idempotent: identical content hashes to the same
version_id, and a re-register returns it without a second event —
*reuse before duplication*, mechanically.

Import cost: stdlib + this package.
"""

from __future__ import annotations

from .base import AssetError, _check_str, _raise_if, utc_now
from .model import AssetModel, model_hash
from .record import AssetRecord, check_payload
from .store import Store

__all__ = ["Registry"]


class Registry:
    """The registry engine over one store governed by one model.

    Parameters
    ----------
    store : Store
        An initialized store; its pin must match ``model``.
    model : AssetModel
        The governing model; hashed and compared against the pin, so a
        store can never be driven by a model it was not created with.

    Examples
    --------
    >>> import tempfile
    >>> from dskit.assets.default_model import default_model
    >>> from dskit.assets.store import FileStore
    >>> m = default_model()
    >>> reg = Registry(FileStore.create(tempfile.mkdtemp(), m), m)
    >>> e = reg.register("entity", {"name": "AAPL"}, origin="doctest")
    >>> f = reg.register("feature", {"name": "mom_20d"}, refs={"entity": e})
    >>> reg.state(f)                      # lifecycle starts at the model's initial
    'draft'
    >>> reg.transition(f, "validated", origin="doctest")
    >>> reg.state(f)
    'validated'
    >>> reg.find("feature", "mom_20d") == [f]
    True
    >>> reg.register("feature", {"name": "mom_20d"}, refs={"entity": e}) == f
    True
    """

    def __init__(self, store, model):
        errors = []
        if not isinstance(store, Store):
            errors.append(f"store must be a Store, got {type(store).__name__}")
        if not isinstance(model, AssetModel):
            errors.append(f"model must be an AssetModel, got {type(model).__name__}")
        _raise_if(errors)
        pin = store.model_pin()
        if model_hash(model) != pin.get("model_hash"):
            raise AssetError(
                [
                    f"model {model.name!r} (hash {model_hash(model)[:12]}...) does not "
                    f"match the store's pin ({pin.get('model_hash', '')[:12]}...) — "
                    "a store is governed by the model it was created with"
                ]
            )
        self.store = store
        self.model = model

    # -- internal ----------------------------------------------------------

    def _spec(self, kind):
        spec = self.model.kinds.get(kind)
        if spec is None:
            raise AssetError(
                [f"kind {kind!r} is not declared by model {self.model.name!r} "
                 f"— declared: {sorted(self.model.kinds)}"]
            )
        return spec

    # -- registration ------------------------------------------------------

    def register(self, kind, payload, refs=None, origin="", notes="") -> str:
        """Validate, persist, and log one asset version; return its version_id.

        Parameters
        ----------
        kind : str
            A kind declared by the governing model.
        payload : dict
            Field name -> JSON value; hash-material.
        refs : dict, optional
            Ref name -> version_id of a record ALREADY in this store,
            of the kind the model declares for that ref.
        origin : str
            Provenance: who is registering (``"cli"``, ``"ingest-run"``, ...).
        notes : str
            Documentation; outside the hash.

        Returns
        -------
        str
            The version_id. Identical content returns the existing id
            with no new event — registration is idempotent.
        """
        spec = self._spec(kind)
        refs = {} if refs is None else refs
        check_payload(spec, payload, refs)

        # The cross-asset check only the registry can make: refs must
        # resolve HERE, to the declared kind — the graph stays closed.
        errors = []
        for n, vid in sorted(refs.items()):
            if not self.store.has_record(vid):
                errors.append(f"refs.{n}: no record {vid!r} in this store")
            else:
                found = self.store.get_record(vid).kind
                want = spec.refs[n].kind
                if found != want:
                    errors.append(f"refs.{n}: {vid!r} is a {found!r}, model requires {want!r}")
        _raise_if(errors)

        record = AssetRecord(
            kind=kind, payload=payload, refs=refs,
            registered_at=utc_now(), origin=origin, notes=notes,
        )
        vid = record.version_id()
        if self.store.has_record(vid):
            return vid  # reuse before duplication: no write, no event
        self.store.put_record(record)
        event = {
            "event": "register", "kind": kind, "version_id": vid,
            "at": record.registered_at, "origin": origin,
        }
        if spec.states:
            event["state"] = spec.initial
        self.store.append_event(event)
        return vid

    # -- reads -------------------------------------------------------------

    def get(self, version_id) -> AssetRecord:
        """Load one record; refuse records of kinds the model never declared."""
        record = self.store.get_record(version_id)
        self._spec(record.kind)  # a foreign kind means an out-of-band write
        return record

    def find(self, kind, name) -> list:
        """version_ids of ``kind`` whose payload ``name`` equals ``name`` —
        the alias lookup of ADR-0009 (aliases may have many versions)."""
        self._spec(kind)
        errors = []
        _check_str(errors, "name", name)
        _raise_if(errors)
        return [
            vid
            for vid in self.store.list_records(kind)
            if self.store.get_record(vid).payload.get("name") == name
        ]

    def list(self, kind=None) -> list:
        """Sorted version_ids, for one declared kind or the whole store."""
        if kind is not None:
            self._spec(kind)
        return self.store.list_records(kind)

    # -- lifecycle ---------------------------------------------------------

    def state(self, version_id):
        """The current lifecycle state, derived by replaying the event log.

        Returns
        -------
        str or None
            The state, or None for a record-only kind (no lifecycle).
        """
        record = self.get(version_id)
        spec = self._spec(record.kind)
        if not spec.states:
            return None
        current = None
        for event in self.store.iter_events():
            if event.get("version_id") != version_id:
                continue
            if event.get("event") == "register":
                current = event.get("state", spec.initial)
            elif event.get("event") == "transition":
                current = event.get("to")
        if current is None:
            raise AssetError(
                [f"{version_id!r} has no register event — store written out-of-band?"]
            )
        if current not in spec.states:
            raise AssetError(
                [f"{version_id!r} replays to undeclared state {current!r} — event log corrupt?"]
            )
        return current

    def transition(self, version_id, to, origin="") -> None:
        """Move an asset to a new lifecycle state, default-deny.

        The move must be listed in the model's transition map for the
        asset's CURRENT state; everything else is refused. The move is an
        appended event — history is immutable, records never change.
        """
        record = self.get(version_id)
        spec = self._spec(record.kind)
        errors = []
        _check_str(errors, "to", to)
        _raise_if(errors)
        if not spec.states:
            raise AssetError(
                [f"kind {record.kind!r} is record-only — it has no lifecycle to transition"]
            )
        current = self.state(version_id)
        allowed = spec.transitions.get(current, ())
        if to not in allowed:
            raise AssetError(
                [f"cannot transition {record.kind!r} from {current!r} to {to!r} — "
                 f"model allows: {sorted(allowed)}"]
            )
        self.store.append_event(
            {
                "event": "transition", "kind": record.kind, "version_id": version_id,
                "from": current, "to": to, "at": utc_now(), "origin": origin,
            }
        )
