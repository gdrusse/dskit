"""Publication — a version manifest into the outbox P1 scans (ADR-0012).

The published root IS the outbox: publishing a dataset version means
writing ONE pointer manifest (Iceberg-style — snapshot hashes, never
data copies) into ``published/<dataset>/`` with maildir discipline.
P1's registrar (``python -m dskit.assets sync-published``) scans that
root and registers idempotently by content hash; losing a "nudge" costs
nothing because the scan is also the anti-entropy repair.

The manifest a consumer sees::

    {"manifest_version": 1,
     "dataset": "<P1 dataset alias>",
     "name": "<version alias>",
     "mode": "backfill" | "live",           # the tracking axis, end to end
     "acquired_at": "...",
     "effective_range": {"start": ..., "end": ...},
     "snapshots": [{"snapshot": vid, "manifest_hash": ...}],
     "certification": vid}

Filename ``NNNNNNNN-<hash8>.json``: the sequence number is a human
courtesy (scan order), the hash is the identity — re-publishing
identical content is detected by hash and reuses the existing file, so
publication is idempotent like everything else.

Governance enforced here: only a ``certified`` decision publishes
(a refusal is evidence, not a license), completing the ADR-0015 chain —
publication without certification is structurally impossible.

Import cost: stdlib + this package.
"""

from __future__ import annotations

import json
import os

from .base import (
    AssetError,
    _check_segment,
    _check_str,
    _raise_if,
    canonical_hash,
    durable_write_bytes,
)
from .layout import OnboardingRoot

__all__ = ["publish_version"]


def publish_version(root, registry, dataset, certification_vid,
                    name="", origin="publish") -> dict:
    """Publish one certified snapshot as a dataset version; return a summary.

    Parameters
    ----------
    root : OnboardingRoot
        The onboarding root whose ``published/`` is the outbox.
    registry : Registry
        The P2 registry holding the certification chain.
    dataset : str
        The P1 dataset alias this version belongs to (filesystem-safe —
        it names the outbox subdirectory).
    certification_vid : str
        A ``certification`` record with decision ``"certified"``.
    name : str
        The version's alias; default ``"<dataset>@NNNNNNNN"``.
    origin : str
        Provenance stamp.

    Returns
    -------
    dict
        ``{"published_version": vid, "manifest_path": path,
        "version_manifest_hash": hash, "reused": bool}`` — ``reused``
        is True when identical content was already in the outbox.
    """
    if not isinstance(root, OnboardingRoot):
        raise AssetError([f"root must be an OnboardingRoot, got {type(root).__name__}"])
    errors = []
    _check_segment(errors, "dataset", dataset)
    _check_str(errors, "certification_vid", certification_vid)
    _check_str(errors, "name", name, non_empty=False)
    _check_str(errors, "origin", origin)
    _raise_if(errors)

    # -- the governance chain: certification -> snapshot, decision checked -
    cert = registry.get(certification_vid)
    if cert.kind != "certification":
        raise AssetError(
            [f"{certification_vid!r} is a {cert.kind!r}, not a certification"]
        )
    if cert.payload["decision"] != "certified":
        raise AssetError(
            [f"certification {certification_vid[:12]}... records "
             f"{cert.payload['decision']!r} — only a certified decision publishes"]
        )
    snap = registry.get(cert.refs["snapshot"])

    # -- idempotency: ONE certification publishes at most ONE manifest ------
    # The idempotency key is the certification (the default label embeds
    # the sequence number, so content-hashing alone would not catch a
    # re-publish). An existing manifest for this certification is reused
    # verbatim, whatever name was requested this time.
    out_dir = root.published_dir(dataset)
    os.makedirs(out_dir, exist_ok=True)
    existing = sorted(f for f in os.listdir(out_dir)
                      if f.endswith(".json") and not f.startswith("."))
    manifest, manifest_path, reused = None, None, False
    for fname in existing:
        path = os.path.join(out_dir, fname)
        try:
            with open(path, encoding="utf-8") as fh:
                prior = json.load(fh)
        except (OSError, ValueError) as exc:
            raise AssetError(
                [f"outbox file {path!r} is unreadable: {exc} — published/ is "
                 "WORM; investigate before publishing more"]
            ) from exc
        if isinstance(prior, dict) and prior.get("certification") == certification_vid:
            manifest, manifest_path, reused = prior, path, True
            label = prior.get("name", "")
            break

    # -- the manifest: pointers, never data ---------------------------------
    if manifest is None:
        seq = len(existing) + 1
        label = name or f"{dataset}@{seq:08d}"
        manifest = {
            "manifest_version": 1,
            "dataset": dataset,
            "name": label,
            "mode": snap.payload["mode"],
            "acquired_at": snap.payload["acquired_at"],
            "effective_range": {
                "start": snap.payload.get("effective_start", ""),
                "end": snap.payload.get("effective_end", ""),
            },
            "snapshots": [{
                "snapshot": cert.refs["snapshot"],
                "manifest_hash": snap.payload["manifest_hash"],
            }],
            "certification": certification_vid,
        }
    vhash = canonical_hash(manifest)
    if manifest_path is None:
        manifest_path = os.path.join(out_dir, f"{seq:08d}-{vhash[:8]}.json")
        payload = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        durable_write_bytes(manifest_path, payload.encode("utf-8"))

    # -- the publication evidence in the P2 store ---------------------------
    version_vid = registry.register(
        "published_version",
        {
            "name": label,
            "dataset": dataset,
            "version_manifest_hash": vhash,
            "mode": snap.payload["mode"],
            "effective_start": snap.payload.get("effective_start", ""),
            "effective_end": snap.payload.get("effective_end", ""),
        },
        refs={"certification": certification_vid},
        origin=origin,
    )
    return {
        "published_version": version_vid,
        "manifest_path": manifest_path,
        "version_manifest_hash": vhash,
        "reused": reused,
    }
