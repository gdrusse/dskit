"""The asset record — one immutable registered version of one asset.

ADR-0009 made concrete: an asset version IS ``{kind, payload, refs}``,
and its ``version_id`` is the canonical hash of exactly that. Everything
else a record carries — ``registered_at``, ``origin``, ``notes`` — is
provenance or documentation, deliberately OUTSIDE the hash: registering
the same content twice, from different places at different times, yields
the same identity (reuse before duplication).

What is deliberately NOT here:

- **Lifecycle state.** A record is a value; state changes over time.
  Transitions are append-only store events (ADR-0010), and the current
  state is derived by the registry — never stored on the record.
- **Cross-asset checks.** Whether a ref's version_id actually resolves,
  and to the declared kind, needs the store — that is the registry's
  job. :func:`check_payload` checks everything a record + its
  :class:`~dskit.assets.model.KindSpec` can decide alone.

One reserved name: the hash recipe strips ``notes`` at every level, so a
payload field or ref named ``"notes"`` would silently vanish from
identity — :func:`check_payload` refuses the name outright.

Import cost: stdlib + :mod:`dskit.assets.base` / :mod:`~dskit.assets.model`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .base import (
    _check_dict,
    _check_str,
    _check_unknown,
    _raise_if,
    canonical_hash,
)
from .model import KindSpec

__all__ = ["AssetRecord", "check_payload"]

#: A version_id is a sha256 hex digest — nothing else. Refusing anything
#: shorter catches "I passed the human alias" bugs at the boundary.
_VERSION_ID = re.compile(r"^[0-9a-f]{64}$")

# The six JSON types as Python predicates. bool is checked FIRST because
# bool subclasses int — a JSON boolean must never pass as a number.
_JSON_CHECKS = {
    "array": lambda v: isinstance(v, list),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "object": lambda v: isinstance(v, dict),
    "string": lambda v: isinstance(v, str),
}


def check_payload(spec, payload, refs):
    """Validate a payload + refs against a kind's spec; raise listing ALL problems.

    Checks: payload keys are declared fields (default-deny), required
    fields present, values match their declared JSON type, refs keys are
    declared refs, required refs present, ref values look like
    version_ids, and the reserved name ``notes`` appears nowhere.

    Parameters
    ----------
    spec : KindSpec
        The kind's declaration from the governing model.
    payload : dict
        Field name -> JSON value.
    refs : dict
        Ref name -> the referenced asset's version_id (64-char sha256 hex).

    Raises
    ------
    AssetError
        Listing every violation at once.

    Examples
    --------
    >>> from dskit.assets.model import FieldSpec, KindSpec, RefSpec
    >>> spec = KindSpec(
    ...     fields={"name": FieldSpec(type="string", required=True),
    ...             "rows": FieldSpec(type="number")},
    ...     refs={"source": RefSpec(kind="source", required=True)})
    >>> check_payload(spec, {"name": "prices"}, {"source": "ab" * 32})  # ok
    >>> check_payload(spec, {"rows": True}, {})
    Traceback (most recent call last):
    ...
    dskit.assets.base.AssetError: invalid asset operation (3 problems):
      payload.rows must be number, got True
      payload missing required field(s) ['name']
      refs missing required ref(s) ['source']
    """
    errors = []
    if not isinstance(spec, KindSpec):
        errors.append(f"spec must be a KindSpec, got {type(spec).__name__}")
    _check_dict(errors, "payload", payload)
    _check_dict(errors, "refs", refs)
    _raise_if(errors)

    # Fields: default-deny, then required, then typed.
    _check_unknown(errors, payload, spec.fields, "payload")
    if "notes" in payload:
        errors.append('payload key "notes" is reserved — it is stripped from identity')
    for n, v in sorted(payload.items()):
        fs = spec.fields.get(n)
        if fs is not None and not _JSON_CHECKS[fs.type](v):
            errors.append(f"payload.{n} must be {fs.type}, got {v!r}")
    missing = sorted(n for n, fs in spec.fields.items() if fs.required and n not in payload)
    if missing:
        errors.append(f"payload missing required field(s) {missing}")

    # Refs: default-deny, required, and version_id-shaped.
    _check_unknown(errors, refs, spec.refs, "refs")
    if "notes" in refs:
        errors.append('ref name "notes" is reserved — it is stripped from identity')
    for n, v in sorted(refs.items()):
        if n in spec.refs and (not isinstance(v, str) or not _VERSION_ID.match(v)):
            errors.append(f"refs.{n} must be a version_id (64-char sha256 hex), got {v!r}")
    missing = sorted(n for n, rs in spec.refs.items() if rs.required and n not in refs)
    if missing:
        errors.append(f"refs missing required ref(s) {missing}")
    _raise_if(errors)


@dataclass(frozen=True, slots=True)
class AssetRecord:
    """One registered asset version — the unit a store holds.

    Parameters
    ----------
    kind : str
        The kind's name in the governing model.
    payload : dict
        Field name -> JSON value; hash-material.
    refs : dict
        Ref name -> referenced version_id; hash-material.
    registered_at : str
        Provenance: when it entered the store (:func:`~dskit.assets.base.utc_now`).
        Outside the hash; empty until the registry stamps it.
    origin : str
        Provenance: who registered it (``"cli"``, ``"ingest-run"``, ...).
    notes : str
        Documentation; outside the hash.

    Examples
    --------
    >>> r = AssetRecord(kind="entity", payload={"name": "AAPL"}, refs={})
    >>> r.version_id() == AssetRecord(kind="entity", payload={"name": "AAPL"},
    ...     refs={}, registered_at="2026-08-22T00:00:00+00:00",
    ...     origin="cli", notes="doc").version_id()   # provenance never changes identity
    True
    >>> len(r.version_id())
    64
    """

    kind: str
    payload: dict
    refs: dict
    registered_at: str = ""
    origin: str = ""
    notes: str = ""

    def __post_init__(self):
        errors = []
        _check_str(errors, "kind", self.kind)
        _check_dict(errors, "payload", self.payload)
        _check_dict(errors, "refs", self.refs)
        _check_str(errors, "registered_at", self.registered_at, non_empty=False)
        _check_str(errors, "origin", self.origin, non_empty=False)
        _check_str(errors, "notes", self.notes, non_empty=False)
        _raise_if(errors)

    def version_id(self) -> str:
        """The record's identity: canonical hash of ``{kind, payload, refs}``.

        Returns
        -------
        str
            Hex sha256. Same content, same id — however and whenever
            registered (ADR-0009).
        """
        return canonical_hash(
            {"kind": self.kind, "payload": self.payload, "refs": self.refs}
        )

    def to_obj(self) -> dict:
        """The store file format: identity material + version_id + provenance.

        The emitted ``version_id`` makes files self-describing and
        tamper-evident — :meth:`from_obj` recomputes it and refuses drift.
        """
        out = {
            "kind": self.kind,
            "payload": dict(self.payload),
            "refs": dict(self.refs),
            "version_id": self.version_id(),
        }
        if self.registered_at:
            out["registered_at"] = self.registered_at
        if self.origin:
            out["origin"] = self.origin
        if self.notes:
            out["notes"] = self.notes
        return out

    @classmethod
    def from_obj(cls, obj) -> "AssetRecord":
        """Rebuild a record from its file form; refuse tampered content.

        Raises
        ------
        AssetError
            On unknown keys, or when a stored ``version_id`` does not
            match the recomputed hash of the stored content.
        """
        errors = []
        _check_dict(errors, "record", obj)
        _raise_if(errors)
        _check_unknown(
            errors, obj,
            ("kind", "payload", "refs", "version_id", "registered_at", "origin", "notes"),
            "record",
        )
        _raise_if(errors)
        rec = cls(
            kind=obj.get("kind", ""),
            payload=obj.get("payload", {}),
            refs=obj.get("refs", {}),
            registered_at=obj.get("registered_at", ""),
            origin=obj.get("origin", ""),
            notes=obj.get("notes", ""),
        )
        stored = obj.get("version_id")
        if stored is not None and stored != rec.version_id():
            errors.append(
                f"stored version_id {stored!r} does not match content hash "
                f"{rec.version_id()!r} — record corrupted or edited"
            )
        _raise_if(errors)
        return rec
