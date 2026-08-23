"""The publication scan — ADR-0012: the published root IS the outbox.

Package 2 (or any producer honoring the manifest shape) publishes
version manifests into ``published/<dataset>/*.json`` with maildir
discipline; THIS module is the consuming half: scan the root, register
every manifest as a ``dataset_version``, assert onboarding-phase
lineage. The scan is simultaneously delivery and anti-entropy — one
code path, safe to run from cron, a nudge, or a shell whenever.

Effectively-once mechanics: at-least-once scan + hash-keyed idempotent
registration (ADR-0009) = re-scanning costs nothing and repairs
anything missed. Ordering is irrelevant — versions are immutable and
independent.

What a manifest must carry (extra keys are TOLERATED and hash-material —
the seam's forward-compat valve, like unknown message types): ``dataset``
(a P1 dataset alias, already cataloged — the catalog owns datasets,
ADR-0003), ``name``, and optionally ``acquired_at`` /
``effective_range`` / ``mode``. The registered version's ``digest`` is
the canonical hash of the manifest itself — the same identity the
producer computed.

A bad manifest fails ITS file and the scan continues: anti-entropy must
repair the repairable, then report the rest (a failed file exits
non-zero so a cron notices).

Import cost: stdlib + this package.
"""

from __future__ import annotations

import json
import os

from .base import AssetError, _check_str, _raise_if, canonical_hash
from .lineage import Lineage
from .record import AssetRecord
from .registry import Registry

__all__ = ["sync_published"]


def _version_payload(manifest) -> dict:
    """Map a manifest onto the ``dataset_version`` kind's fields."""
    rng = manifest.get("effective_range", {})
    start, end = rng.get("start", ""), rng.get("end", "")
    # One effective_date field, ISO-8601 interval notation for a range —
    # the bitemporal pair's world-time half, flattened for the catalog.
    effective = start if start == end else f"{start}/{end}"
    payload = {"name": manifest["name"], "digest": canonical_hash(manifest)}
    if effective:
        payload["effective_date"] = effective
    if manifest.get("acquired_at"):
        payload["acquisition_date"] = manifest["acquired_at"]
    return payload


def _sync_one(registry, lineage, path, origin):
    """Register one manifest file; return (vid, existed, edges_added)."""
    try:
        with open(path, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, ValueError) as exc:
        raise AssetError([f"cannot read manifest: {exc}"]) from exc
    if not isinstance(manifest, dict):
        raise AssetError(["manifest must be a JSON object"])
    errors = []
    _check_str(errors, "manifest dataset", manifest.get("dataset", ""))
    _check_str(errors, "manifest name", manifest.get("name", ""))
    _raise_if(errors)

    # The catalog owns datasets (ADR-0003): the alias must already
    # resolve, unambiguously, in THIS store.
    dataset_alias = manifest["dataset"]
    candidates = registry.find("dataset", dataset_alias)
    if not candidates:
        raise AssetError(
            [f"dataset {dataset_alias!r} is not cataloged — register the "
             "dataset (and its source) before syncing its versions"]
        )
    if len(candidates) > 1:
        raise AssetError(
            [f"dataset alias {dataset_alias!r} is ambiguous "
             f"({len(candidates)} versions) — cannot pick a parent"]
        )
    dataset_vid = candidates[0]

    payload = _version_payload(manifest)
    refs = {"dataset": dataset_vid}
    existed = registry.store.has_record(
        AssetRecord(kind="dataset_version", payload=payload, refs=refs).version_id()
    )
    vid = registry.register("dataset_version", payload, refs=refs, origin=origin)

    # Onboarding-phase lineage (ADR-0004): the version derives from the
    # dataset's source — the traceability chain the spec demands, with
    # snapshot provenance living in the manifest the digest pins.
    source_vid = registry.get(dataset_vid).refs["source"]
    edges = lineage.add(source_vid, vid, relation="onboarded",
                        phase="onboarding", origin=origin)
    return vid, existed, int(edges)


def sync_published(registry, published_root, origin="sync-published") -> dict:
    """Scan a published root; register every version manifest found.

    Parameters
    ----------
    registry : Registry
        The P1 catalog registry (its model must declare ``dataset`` and
        ``dataset_version`` — the default model does).
    published_root : str
        The outbox root: ``<root>/<dataset>/*.json`` manifests.
    origin : str
        Provenance stamp for records and edges.

    Returns
    -------
    dict
        ``{"registered": [vids], "existing": n, "edges_added": n,
        "failed": [{"file", "error"}]}`` — ``registered`` lists NEW
        versions only; a rescan of a synced root registers nothing,
        fails nothing, and is free.
    """
    errors = []
    if not isinstance(registry, Registry):
        errors.append(f"registry must be a Registry, got {type(registry).__name__}")
    _check_str(errors, "published_root", published_root)
    _check_str(errors, "origin", origin)
    _raise_if(errors)
    published_root = os.path.abspath(os.path.expanduser(published_root))
    if not os.path.isdir(published_root):
        raise AssetError([f"published_root does not exist: {published_root!r}"])

    lineage = Lineage(registry)
    registered, existing, edges_added, failed = [], 0, 0, []
    for dataset_dir in sorted(os.listdir(published_root)):
        full = os.path.join(published_root, dataset_dir)
        if not os.path.isdir(full) or dataset_dir.startswith("."):
            continue
        for fname in sorted(os.listdir(full)):
            if not fname.endswith(".json") or fname.startswith("."):
                continue
            path = os.path.join(full, fname)
            rel = os.path.relpath(path, published_root)
            try:
                vid, existed, edges = _sync_one(registry, lineage, path, origin)
            except AssetError as exc:
                # Anti-entropy: repair what is repairable, report the rest.
                failed.append({"file": rel, "error": "; ".join(exc.errors)})
                continue
            if existed:
                existing += 1
            else:
                registered.append(vid)
            edges_added += edges
    return {"registered": registered, "existing": existing,
            "edges_added": edges_added, "failed": failed}
