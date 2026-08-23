"""The built-in onboarding model — Package 2's domain, shipped as data.

ADR-0007 applied to Package 2 (via ADR-0013): the onboarding domain is
not modules, it is ONE assets-engine store governed by THIS document.
The object below is the ratified ``docs/architecture/onboarding-model.json``
verbatim — a parity test asserts the two hash identically, so the
architecture document and the shipped default can never drift apart.

The design's rulings, visible as topology:

- **Evidence is record-only.** ``acquisition_job``, ``snapshot``,
  ``validation_result``, ``certification``, ``published_version`` have
  no lifecycle — history IS the record; a refusal is also evidence.
- **Only config is governed.** ``source_config`` alone has states
  (draft -> active -> retired): config is managed, evidence is immutable.
- **Mode is first-class** (ADR-0014): a required field on every job,
  stamped onto every snapshot — backfill vs live is declared, never
  inferred from dates.
- **The chain is the refs**: snapshot -> job -> source_config;
  certification -> {snapshot, result}; published_version ->
  certification. Publication without a certificate is structurally
  impossible (ADR-0015).

Import cost: stdlib + :mod:`dskit.assets`.
"""

from __future__ import annotations

from dskit.assets.model import AssetModel

__all__ = ["onboarding_model"]


def onboarding_model() -> AssetModel:
    """Build and validate the ratified Package 2 model.

    Returns
    -------
    AssetModel
        The validated model named ``"onboarding"`` — six kinds, one
        lifecycle, hash-pinned against the architecture document.

    Examples
    --------
    >>> m = onboarding_model()
    >>> sorted(m.kinds)  # doctest: +NORMALIZE_WHITESPACE
    ['acquisition_job', 'certification', 'published_version', 'snapshot',
     'source_config', 'validation_result']
    >>> sorted(k for k, ks in m.kinds.items() if ks.states)  # governed kinds
    ['source_config']
    >>> m.kinds["acquisition_job"].fields["mode"].required   # mode is first-class
    True
    """
    obj = {
        "name": "onboarding",
        "notes": (
            "Package 2's domain model — the spec's objects as kinds (ADR-0007: "
            "config, not code). A P2-local assets store is governed by this "
            "document. Evidence kinds (job, snapshot, validation_result, "
            "certification, published_version) are record-only: registered "
            "once, never transitioned — history is the record. Only "
            "source_config, the operational handle, has a lifecycle. The "
            "catalog-of-record Source lives in P1 (ADR-0003); source_config "
            "references it by alias. Validate + explore: python -m dskit.assets "
            "init --store /tmp/ob --model docs/architecture/onboarding-model.json"
        ),
        "kinds": {
            "source_config": {
                "notes": (
                    "Operational connector config for one P1 catalog source "
                    "(ADR-0003): credentials location, connector choice, stream "
                    "selection. The only governed kind here — config is "
                    "managed, evidence is immutable."
                ),
                "fields": {
                    "name": {"type": "string", "required": True},
                    "catalog_source": {
                        "type": "string", "required": True,
                        "notes": "The P1 Source this operates for — alias or "
                                 "version_id in the catalog store.",
                    },
                    "connector": {
                        "type": "string", "required": True,
                        "notes": "Registered connector kind or pkg.module:Class, "
                                 "as with pipeline nodes.",
                    },
                    "config": {
                        "type": "object",
                        "notes": "The connector's knobs, validated default-deny "
                                 "by its spec(). Secrets stay OUT — referenced "
                                 "via env, never hash-material.",
                    },
                },
                "lifecycle": {
                    "states": ["draft", "active", "retired"],
                    "initial": "draft",
                    "transitions": {"draft": ["active"], "active": ["retired"]},
                },
            },
            "acquisition_job": {
                "notes": (
                    "One invocation of a connector pull. MODE IS FIRST-CLASS: "
                    "backfill (pulling history) vs live (pulling forward) is "
                    "declared here, stamped downstream, and checkpointed "
                    "separately per (source, stream, mode)."
                ),
                "fields": {
                    "name": {"type": "string", "required": True},
                    "mode": {
                        "type": "string", "required": True,
                        "notes": "backfill | live — the project's tracking axis; "
                                 "a declared fact, never inferred from dates.",
                    },
                    "stream": {"type": "string", "required": True},
                    "effective_range": {
                        "type": "object",
                        "notes": "{start, end}: the window of world-time this "
                                 "pull covers. Past window = backfill; "
                                 "now-window = live.",
                    },
                    "status": {
                        "type": "string",
                        "notes": "ran | failed — evidence of what happened, not "
                                 "a lifecycle.",
                    },
                },
                "refs": {
                    "source_config": {"kind": "source_config", "required": True},
                },
            },
            "snapshot": {
                "notes": (
                    "One immutable raw acquisition (bronze). Identity anchors "
                    "on the manifest's Merkle hash of payload bytes; the "
                    "directory path is provenance. Bitemporal pair carried "
                    "explicitly (ADR-0014)."
                ),
                "fields": {
                    "name": {"type": "string", "required": True},
                    "manifest_hash": {
                        "type": "string", "required": True,
                        "notes": "sha256 of the canonical manifest.json — "
                                 "re-hash to verify, tamper-evident.",
                    },
                    "mode": {"type": "string", "required": True},
                    "acquired_at": {"type": "string", "required": True},
                    "effective_start": {"type": "string"},
                    "effective_end": {"type": "string"},
                },
                "refs": {
                    "job": {"kind": "acquisition_job", "required": True},
                },
            },
            "validation_result": {
                "notes": (
                    "The content-addressed outcome of running one suite against "
                    "one snapshot (ADR-0015). Certification consumes THIS, "
                    "never the data."
                ),
                "fields": {
                    "name": {"type": "string", "required": True},
                    "suite_hash": {"type": "string", "required": True},
                    "gating": {
                        "type": "string", "required": True,
                        "notes": "pass | warn | block — thresholds on failing "
                                 "counts; warn never blocks.",
                    },
                    "statistics": {"type": "object"},
                },
                "refs": {
                    "snapshot": {"kind": "snapshot", "required": True},
                },
            },
            "certification": {
                "notes": (
                    "The human/policy decision over a validation result. "
                    "Publish requires one; a refusal is also a record."
                ),
                "fields": {
                    "name": {"type": "string", "required": True},
                    "decision": {
                        "type": "string", "required": True,
                        "notes": "certified | refused",
                    },
                    "certified_by": {"type": "string"},
                },
                "refs": {
                    "snapshot": {"kind": "snapshot", "required": True},
                    "result": {"kind": "validation_result", "required": True},
                },
            },
            "published_version": {
                "notes": (
                    "P2's record of one published DatasetVersion: a pointer "
                    "manifest (Iceberg-style, no data copies) written to the "
                    "published root that P1 scans (ADR-0012). The P1 "
                    "registration is the system of record; this is the "
                    "publication evidence."
                ),
                "fields": {
                    "name": {"type": "string", "required": True},
                    "dataset": {
                        "type": "string", "required": True,
                        "notes": "The P1 dataset alias this version belongs to.",
                    },
                    "version_manifest_hash": {"type": "string", "required": True},
                    "mode": {
                        "type": "string",
                        "notes": "Dominant mode of member snapshots — keeps "
                                 "backfill vs live traceable through to "
                                 "publication.",
                    },
                    "effective_start": {"type": "string"},
                    "effective_end": {"type": "string"},
                },
                "refs": {
                    "certification": {"kind": "certification", "required": True},
                },
            },
        },
    }
    return AssetModel.from_obj(obj)
