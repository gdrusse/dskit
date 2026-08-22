"""The asset model — the JSON document that declares what a store governs.

ADR-0007: the engine is generic; a MODEL declares the kinds. This module
defines what a model IS: for every kind, its payload fields (typed with
the six JSON types — structure only, no semantics), its refs to other
kinds, and its lifecycle. The spec's 13 registries arrive as data
(:mod:`~dskit.assets.default_model`), never as code.

Rules inherited from the pipeline's config layer:

- **An invalid model can never exist.** Every spec validates in
  ``__post_init__``; there is no separate ``validate()`` to forget.
- **Errors accumulate.** ``from_obj`` reports EVERY problem in one
  :class:`~dskit.assets.base.AssetError`, each prefixed with its path
  (``kinds.dataset.fields.name``), and REJECTS unknown keys loudly.
- **Canonical round-trip + content hash.** ``to_obj()`` omits empty
  sections and default flags, so ``from_obj(to_obj(m))`` reproduces the
  same object and :func:`model_hash` is stable. Governance is exactly as
  strong as the pinned model hash (ADR-0007).
- **Every kind declares a required string ``name``** — the human alias
  beside the content-hash identity (ADR-0009).

A kind with no lifecycle ``states`` is **record-only** (observations,
artifacts): registered once, never transitioned.

Import cost: stdlib + :mod:`dskit.assets.base`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .base import (
    AssetError,
    _check_dict,
    _check_str,
    _check_unknown,
    _raise_if,
    canonical_hash,
)

__all__ = [
    "AssetModel",
    "FieldSpec",
    "JSON_TYPES",
    "KindSpec",
    "RefSpec",
    "load_model",
    "model_hash",
]

#: The six JSON types (ADR-0007) — the ONLY vocabulary a field may use.
#: The engine checks structure; meaning belongs to whoever wrote the model.
JSON_TYPES = ("array", "boolean", "null", "number", "object", "string")


def _merge_child(errors, where, exc):
    """Fold a child's AssetError into the parent's list, path-prefixed."""
    errors.extend(f"{where}: {e}" for e in exc.errors)


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """One payload field of a kind: a JSON type plus a required flag.

    Parameters
    ----------
    type : str
        One of :data:`JSON_TYPES`.
    required : bool
        Whether a record of this kind must supply the field.
    notes : str
        Documentation; excluded from the model hash.

    Examples
    --------
    >>> FieldSpec(type="string", required=True)   # the mandatory name field
    FieldSpec(type='string', required=True, notes='')
    >>> FieldSpec(type="number", notes="row count at certification")
    FieldSpec(type='number', required=False, notes='row count at certification')

    JSON form: ``{"type": "number", "notes": "row count at certification"}``.
    """

    type: str
    required: bool = False
    notes: str = ""

    def __post_init__(self):
        errors = []
        if self.type not in JSON_TYPES:
            errors.append(f"type must be one of {list(JSON_TYPES)}, got {self.type!r}")
        if not isinstance(self.required, bool):
            errors.append(f"required must be a bool, got {self.required!r}")
        _check_str(errors, "notes", self.notes, non_empty=False)
        _raise_if(errors)

    def to_obj(self) -> dict:
        out = {"type": self.type}
        if self.required:
            out["required"] = True
        if self.notes:
            out["notes"] = self.notes
        return out

    @classmethod
    def from_obj(cls, obj) -> "FieldSpec":
        errors = []
        _check_dict(errors, "field spec", obj)
        _raise_if(errors)
        _check_unknown(errors, obj, ("type", "required", "notes"))
        _raise_if(errors)
        return cls(
            type=obj.get("type", ""),
            required=obj.get("required", False),
            notes=obj.get("notes", ""),
        )


@dataclass(frozen=True, slots=True)
class RefSpec:
    """One reference from a kind to another kind — the topology governance
    of ADR-0007 (a feature REQUIRES an entity ref; a target has none).

    Parameters
    ----------
    kind : str
        The target kind's name; must be declared in the same model.
    required : bool
        Whether a record of the owning kind must supply this ref.
    notes : str
        Documentation; excluded from the model hash.

    Examples
    --------
    >>> RefSpec(kind="entity", required=True)   # a feature must name its entity
    RefSpec(kind='entity', required=True, notes='')
    >>> RefSpec(kind="source")                  # optional provenance pointer
    RefSpec(kind='source', required=False, notes='')

    JSON form: ``{"kind": "entity", "required": true}``.
    """

    kind: str
    required: bool = False
    notes: str = ""

    def __post_init__(self):
        errors = []
        _check_str(errors, "kind", self.kind)
        if not isinstance(self.required, bool):
            errors.append(f"required must be a bool, got {self.required!r}")
        _check_str(errors, "notes", self.notes, non_empty=False)
        _raise_if(errors)

    def to_obj(self) -> dict:
        out = {"kind": self.kind}
        if self.required:
            out["required"] = True
        if self.notes:
            out["notes"] = self.notes
        return out

    @classmethod
    def from_obj(cls, obj) -> "RefSpec":
        errors = []
        _check_dict(errors, "ref spec", obj)
        _raise_if(errors)
        _check_unknown(errors, obj, ("kind", "required", "notes"))
        _raise_if(errors)
        return cls(
            kind=obj.get("kind", ""),
            required=obj.get("required", False),
            notes=obj.get("notes", ""),
        )


@dataclass(frozen=True, slots=True)
class KindSpec:
    """One kind of asset: its fields, its refs, and its lifecycle.

    Parameters
    ----------
    fields : dict
        Field name -> :class:`FieldSpec`. Must include a required string
        ``name`` — the human alias mandated by ADR-0009.
    refs : dict
        Ref name -> :class:`RefSpec`.
    states : tuple
        Lifecycle states in declaration order. Empty = record-only.
    initial : str
        The state a record is registered in; one of ``states``.
    transitions : dict
        State -> tuple of states reachable from it. Any pair not listed
        is a refused transition — default-deny, like params.
    notes : str
        Documentation; excluded from the model hash.

    Examples
    --------
    A governed kind with a lifecycle:

    >>> dataset = KindSpec(
    ...     fields={"name": FieldSpec(type="string", required=True),
    ...             "rows": FieldSpec(type="number")},
    ...     refs={"source": RefSpec(kind="source", required=True)},
    ...     states=("draft", "certified", "retired"),
    ...     initial="draft",
    ...     transitions={"draft": ("certified",), "certified": ("retired",)},
    ... )

    A record-only kind — no lifecycle at all (ADR-0007: observations are
    registered once, never transitioned):

    >>> observation = KindSpec(
    ...     fields={"name": FieldSpec(type="string", required=True)})

    JSON form of ``dataset``::

        {"fields": {"name": {"type": "string", "required": true},
                    "rows": {"type": "number"}},
         "refs":   {"source": {"kind": "source", "required": true}},
         "lifecycle": {"states": ["draft", "certified", "retired"],
                       "initial": "draft",
                       "transitions": {"draft": ["certified"],
                                       "certified": ["retired"]}}}
    """

    fields: dict
    refs: dict = field(default_factory=dict)
    states: tuple = ()
    initial: str = ""
    transitions: dict = field(default_factory=dict)
    notes: str = ""

    def __post_init__(self):
        errors = []
        _check_dict(errors, "fields", self.fields)
        _check_dict(errors, "refs", self.refs)
        _check_str(errors, "notes", self.notes, non_empty=False)
        _raise_if(errors)
        for n, fs in self.fields.items():
            if not isinstance(fs, FieldSpec):
                errors.append(f"fields.{n} must be a FieldSpec, got {type(fs).__name__}")
        for n, rs in self.refs.items():
            if not isinstance(rs, RefSpec):
                errors.append(f"refs.{n} must be a RefSpec, got {type(rs).__name__}")
        _raise_if(errors)

        # ADR-0009: the human alias is not optional and not negotiable.
        name = self.fields.get("name")
        if name is None or name.type != "string" or not name.required:
            errors.append('fields must declare "name" as a required string (ADR-0009)')

        # Lifecycle: absent entirely (record-only) or internally consistent.
        if not isinstance(self.states, tuple) or any(
            not isinstance(s, str) or not s for s in self.states
        ):
            errors.append(
                f"lifecycle.states must be a tuple of non-empty strings, got {self.states!r}"
            )
        elif len(set(self.states)) != len(self.states):
            errors.append(f"lifecycle.states must be unique, got {list(self.states)}")
        elif not self.states:
            if self.initial or self.transitions:
                errors.append("record-only kind (no states) must have no initial/transitions")
        else:
            if self.initial not in self.states:
                errors.append(
                    f"lifecycle.initial must be one of {list(self.states)}, got {self.initial!r}"
                )
            _check_dict(errors, "lifecycle.transitions", self.transitions)
            if not errors:
                for src, dsts in self.transitions.items():
                    if src not in self.states:
                        errors.append(
                            f"lifecycle.transitions key {src!r} is not a declared state"
                        )
                    if not isinstance(dsts, tuple) or any(d not in self.states for d in dsts):
                        errors.append(
                            f"lifecycle.transitions.{src} must be a tuple of declared states, got {dsts!r}"
                        )
        _raise_if(errors)

    def to_obj(self) -> dict:
        out = {"fields": {n: fs.to_obj() for n, fs in sorted(self.fields.items())}}
        if self.refs:
            out["refs"] = {n: rs.to_obj() for n, rs in sorted(self.refs.items())}
        if self.states:
            out["lifecycle"] = {
                "states": list(self.states),
                "initial": self.initial,
                "transitions": {s: list(d) for s, d in sorted(self.transitions.items())},
            }
        if self.notes:
            out["notes"] = self.notes
        return out

    @classmethod
    def from_obj(cls, obj) -> "KindSpec":
        errors = []
        _check_dict(errors, "kind spec", obj)
        _raise_if(errors)
        _check_unknown(errors, obj, ("fields", "refs", "lifecycle", "notes"))
        _raise_if(errors)

        fields_in, refs_in = obj.get("fields", {}), obj.get("refs", {})
        _check_dict(errors, "fields", fields_in)
        _check_dict(errors, "refs", refs_in)
        _raise_if(errors)

        # Parse every child before raising, so one report names them all.
        fields_out, refs_out = {}, {}
        for n, o in fields_in.items():
            try:
                fields_out[n] = FieldSpec.from_obj(o)
            except AssetError as exc:
                _merge_child(errors, f"fields.{n}", exc)
        for n, o in refs_in.items():
            try:
                refs_out[n] = RefSpec.from_obj(o)
            except AssetError as exc:
                _merge_child(errors, f"refs.{n}", exc)

        lc = obj.get("lifecycle", {})
        _check_dict(errors, "lifecycle", lc)
        _raise_if(errors)
        _check_unknown(errors, lc, ("states", "initial", "transitions"), "lifecycle")
        _raise_if(errors)
        transitions = lc.get("transitions", {})
        if not isinstance(transitions, dict):
            errors.append(f"lifecycle.transitions must be a dict, got {transitions!r}")
            _raise_if(errors)

        return cls(
            fields=fields_out,
            refs=refs_out,
            states=tuple(lc.get("states", ())),
            initial=lc.get("initial", ""),
            transitions={
                s: tuple(d) if isinstance(d, list) else d for s, d in transitions.items()
            },
            notes=obj.get("notes", ""),
        )


@dataclass(frozen=True, slots=True)
class AssetModel:
    """A complete asset model: named, hashable, and closed over its refs.

    Parameters
    ----------
    name : str
        The model's human name (``"default"``, ``"onboarding"``, ...).
    kinds : dict
        Kind name -> :class:`KindSpec`; at least one. Every ref target
        must be a declared kind — the model is a closed graph.
    notes : str
        Documentation; excluded from the model hash.

    Examples
    --------
    >>> model = AssetModel(
    ...     name="minimal",
    ...     kinds={
    ...         "source": KindSpec(
    ...             fields={"name": FieldSpec(type="string", required=True)}),
    ...         "dataset": KindSpec(
    ...             fields={"name": FieldSpec(type="string", required=True)},
    ...             refs={"source": RefSpec(kind="source", required=True)}),
    ...     },
    ... )
    >>> len(model_hash(model))    # sha256 hex — the governance pin
    64

    The same model as a JSON document (what :func:`load_model` reads)::

        {"name": "minimal",
         "kinds": {
           "source":  {"fields": {"name": {"type": "string", "required": true}}},
           "dataset": {"fields": {"name": {"type": "string", "required": true}},
                       "refs":   {"source": {"kind": "source", "required": true}}}}}
    """

    name: str
    kinds: dict
    notes: str = ""

    def __post_init__(self):
        errors = []
        _check_str(errors, "name", self.name)
        _check_dict(errors, "kinds", self.kinds)
        _check_str(errors, "notes", self.notes, non_empty=False)
        _raise_if(errors)
        if not self.kinds:
            errors.append("kinds must declare at least one kind")
        for n, ks in self.kinds.items():
            if not isinstance(ks, KindSpec):
                errors.append(f"kinds.{n} must be a KindSpec, got {type(ks).__name__}")
        _raise_if(errors)
        for n, ks in self.kinds.items():
            for rn, rs in ks.refs.items():
                if rs.kind not in self.kinds:
                    errors.append(
                        f"kinds.{n}.refs.{rn} targets undeclared kind {rs.kind!r}"
                    )
        _raise_if(errors)

    def to_obj(self) -> dict:
        out = {
            "name": self.name,
            "kinds": {n: ks.to_obj() for n, ks in sorted(self.kinds.items())},
        }
        if self.notes:
            out["notes"] = self.notes
        return out

    @classmethod
    def from_obj(cls, obj) -> "AssetModel":
        errors = []
        _check_dict(errors, "model", obj)
        _raise_if(errors)
        _check_unknown(errors, obj, ("name", "kinds", "notes"))
        kinds_in = obj.get("kinds", {})
        _check_dict(errors, "kinds", kinds_in)
        _raise_if(errors)
        kinds_out = {}
        for n, o in kinds_in.items():
            try:
                kinds_out[n] = KindSpec.from_obj(o)
            except AssetError as exc:
                _merge_child(errors, f"kinds.{n}", exc)
        _raise_if(errors)
        return cls(
            name=obj.get("name", ""),
            kinds=kinds_out,
            notes=obj.get("notes", ""),
        )


def load_model(path) -> AssetModel:
    """Read and validate an asset model from a JSON file.

    Parameters
    ----------
    path : str
        Path to the model document.

    Returns
    -------
    AssetModel
        The validated model.

    Raises
    ------
    AssetError
        If the file is unreadable, not JSON, or not a valid model.

    Examples
    --------
    >>> model = load_model("examples/assets/model.json")   # doctest: +SKIP
    """
    try:
        with open(path, encoding="utf-8") as fh:
            obj = json.load(fh)
    except OSError as exc:
        raise AssetError([f"cannot read model file {path!r}: {exc}"]) from exc
    except ValueError as exc:
        raise AssetError([f"model file {path!r} is not valid JSON: {exc}"]) from exc
    return AssetModel.from_obj(obj)


def model_hash(model) -> str:
    """sha256 identity of a model's canonical form (notes stripped).

    Governance is exactly as strong as this pin (ADR-0007): a store
    records the hash of the model that governs it, so any change to what
    the model PERMITS is a new, visible identity.

    Parameters
    ----------
    model : AssetModel
        The model to identify.

    Returns
    -------
    str
        Hex sha256 digest.
    """
    if not isinstance(model, AssetModel):
        raise AssetError([f"model must be an AssetModel, got {type(model).__name__}"])
    return canonical_hash(model.to_obj())
