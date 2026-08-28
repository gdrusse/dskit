"""The built-in asset model — the spec's 13 registries, shipped as data.

ADR-0007: Package 1's registries are not thirteen modules; they are ONE
engine plus THIS document. Twelve kinds below + lineage native in the
engine = the spec's 13. The model is expressed as the same JSON-ready
object a user's model file would contain, and it is validated through
the same :meth:`~dskit.assets.model.AssetModel.from_obj` path — the
default model earns no shortcuts.

The governance the spec states in prose lives here as topology:

- *"Features belong to entities"* → ``feature.refs.entity`` is required.
- *"Targets are independent assets ... joined to features only in
  run-specific artifacts"* → ``target`` declares NO feature ref.
- *"Every certified dataset must be traceable to a source"* →
  ``dataset.refs.source`` is required; versions chain through datasets.
- *"Observe execution rather than manage it"* → ``run_observation``,
  ``artifact``, ``output`` are record-only: no lifecycle, registered by
  ingest (ADR-0008), never transitioned.

Anything stronger than structure (set membership integrity, date
semantics) is the declared future semantic seam — the engine checks
JSON types; meaning is documented in ``notes``.

Import cost: stdlib + :mod:`dskit.assets.model`.
"""

from __future__ import annotations

from .model import AssetModel

__all__ = ["DEFAULT_LIFECYCLE", "default_model"]

#: The spec's lifecycle, verbatim: Draft -> Validated -> Certified ->
#: Published -> Deprecated -> Retired. Strictly the spec's arrow chain —
#: any pair not listed is refused (default-deny). A project wanting looser
#: flow ships its own model; this one IS the spec.
DEFAULT_LIFECYCLE = {
    "states": ["draft", "validated", "certified", "published", "deprecated", "retired"],
    "initial": "draft",
    "transitions": {
        "draft": ["validated"],
        "validated": ["certified"],
        "certified": ["published"],
        "published": ["deprecated"],
        "deprecated": ["retired"],
    },
}

# Every kind carries this — the human alias beside the content hash
# (ADR-0009). Declared once so the twelve kinds cannot drift.
_NAME = {"type": "string", "required": True, "notes": "Human alias (ADR-0009)."}
_DESC = {"type": "string", "notes": "Free-text description for catalog browsing."}


def default_model() -> AssetModel:
    """Build and validate the default model — the spec's registries.

    Returns
    -------
    AssetModel
        The validated model named ``"default"``, with the twelve kinds.

    Examples
    --------
    Build the model and inspect its kinds::

        m = default_model()
        len(m.kinds)
        # -> 12
        sorted(k for k, ks in m.kinds.items() if not ks.states)  # record-only
        # -> ['artifact', 'output', 'run_observation']
        m.kinds["feature"].refs["entity"].required     # features belong to entities
        # -> True
        "feature" in str(m.kinds["target"].refs)       # targets ref no feature
        # -> False
    """
    obj = {
        "name": "default",
        "notes": (
            "The master spec's 13 registries: these 12 kinds + lineage native "
            "in the engine (ADR-0007). Governance is the ref topology; "
            "semantics beyond JSON structure are documented, not enforced."
        ),
        "kinds": {
            # -- durable governed assets (full spec lifecycle) --------------
            "source": {
                "notes": "Where data comes from — the catalog of record (ADR-0003); "
                         "P2 holds only operational connector config against these ids.",
                "fields": {
                    "name": _NAME,
                    "description": _DESC,
                    "source_type": {"type": "string",
                                    "notes": "api | database | hadoop | file | vendor — free string, P2's taxonomy."},
                },
                "lifecycle": DEFAULT_LIFECYCLE,
            },
            "entity": {
                "notes": "The thing analytics is ABOUT (entity-centric design). "
                         "Features and targets attach here, never to models.",
                "fields": {"name": _NAME, "description": _DESC},
                "lifecycle": DEFAULT_LIFECYCLE,
            },
            "dataset": {
                "notes": "A logical series of data; versions are the immutable cuts.",
                "fields": {"name": _NAME, "description": _DESC},
                "refs": {
                    "source": {"kind": "source", "required": True,
                               "notes": "Every certified dataset is traceable to a source (spec rule)."},
                },
                "lifecycle": DEFAULT_LIFECYCLE,
            },
            "dataset_version": {
                "notes": "One immutable cut of a dataset — created and published by P2, "
                         "registered here as the record of truth (ADR-0002).",
                "fields": {
                    "name": _NAME,
                    "effective_date": {"type": "string",
                                       "notes": "What time the data DESCRIBES (P2 time-series rule)."},
                    "acquisition_date": {"type": "string",
                                         "notes": "When the data was ACQUIRED — kept distinct from effective_date."},
                    "digest": {"type": "string",
                               "notes": "Content digest of the underlying data; storage location is "
                                        "provenance, never identity."},
                    "schema": {"type": "object",
                               "notes": "Column/type layout as published; structure only."},
                },
                "refs": {
                    "dataset": {"kind": "dataset", "required": True,
                                "notes": "The series this version cuts; source traceability chains through it."},
                },
                "lifecycle": DEFAULT_LIFECYCLE,
            },
            "data_product": {
                "notes": "A curated bundle of assets offered for consumption.",
                "fields": {
                    "name": _NAME,
                    "description": _DESC,
                    "members": {"type": "array",
                                "notes": "version_ids of bundled assets; membership integrity is the "
                                         "semantic seam, not engine-checked."},
                },
                "lifecycle": DEFAULT_LIFECYCLE,
            },
            "feature": {
                "notes": "A reusable, entity-owned analytical input (spec governance: "
                         "features belong to entities, never to models).",
                "fields": {"name": _NAME, "description": _DESC},
                "refs": {
                    "entity": {"kind": "entity", "required": True,
                               "notes": "THE governance rule — a feature without an entity is refused."},
                },
                "lifecycle": DEFAULT_LIFECYCLE,
            },
            "feature_version": {
                "notes": "One immutable definition of a feature — computed by the "
                         "engine, registered here (OQ-3 seam).",
                "fields": {
                    "name": _NAME,
                    "definition": {"type": "object",
                                   "notes": "The transformation declaration (e.g. a pipeline node's "
                                            "uses/params) — WHAT computes it, hash-material."},
                },
                "refs": {
                    "feature": {"kind": "feature", "required": True},
                    "dataset_version": {"kind": "dataset_version",
                                        "notes": "The governed input it was computed from, when known."},
                },
                "lifecycle": DEFAULT_LIFECYCLE,
            },
            "feature_set": {
                "notes": "A named selection of feature versions used together.",
                "fields": {
                    "name": _NAME,
                    "description": _DESC,
                    "members": {"type": "array", "required": True,
                                "notes": "feature_version version_ids; integrity is the semantic seam."},
                },
                "lifecycle": DEFAULT_LIFECYCLE,
            },
            "target": {
                "notes": "An independent asset (spec rule): stored apart from features, "
                         "joined to them only in run-specific artifacts — hence NO feature ref.",
                "fields": {
                    "name": _NAME,
                    "description": _DESC,
                    "horizon": {"type": "string",
                                "notes": "Forward window the target realizes over, if applicable."},
                },
                "refs": {
                    "entity": {"kind": "entity",
                               "notes": "What the target is about; optional — some targets are portfolio-level."},
                },
                "lifecycle": DEFAULT_LIFECYCLE,
            },
            # -- execution observations (record-only; ingested per ADR-0008) --
            "run_observation": {
                "notes": "One completed pipeline run, observed never managed (ADR-0005); "
                         "connections to inputs/outputs live in the lineage graph.",
                "fields": {
                    "name": _NAME,
                    "config_hash": {"type": "string",
                                    "notes": "The run's identity hash — same hash, same experiment."},
                    "asof": {"type": "string", "notes": "The run's asof date."},
                    "status": {"type": "string", "notes": "ran | halted | error — the engine's verdict."},
                    "summary": {"type": "object", "notes": "Result summary as reported by the run."},
                },
            },
            "artifact": {
                "notes": "A durable file a run produced (model weights, report, plot).",
                "fields": {
                    "name": _NAME,
                    "digest": {"type": "string",
                               "notes": "Content digest of the file; path is provenance, never identity."},
                    "media_type": {"type": "string", "notes": "What the bytes are (e.g. application/json)."},
                },
                "refs": {
                    "run": {"kind": "run_observation", "required": True,
                            "notes": "The run that produced it — artifacts never float free."},
                },
            },
            "output": {
                "notes": "An analytical result a run emitted (signal, forecast, decision). "
                         "Forecast homing is OQ-6; they land here until it closes.",
                "fields": {
                    "name": _NAME,
                    "summary": {"type": "object", "notes": "The result's declared content or digest thereof."},
                },
                "refs": {
                    "run": {"kind": "run_observation", "required": True},
                },
            },
        },
    }
    return AssetModel.from_obj(obj)
