"""The observation seam — ADR-0008: observe runs by reading their dirs.

The pipeline's purity gate forbids it importing any dskit sibling, and
ADR-0005 forbids this package triggering execution — so the ONLY thing
that crosses the boundary is a directory on disk. A completed run dir
holds ``result.json`` (the verdict), ``nodes/NN-<key>.json`` (per-node
records), and ``artifacts/<node>/...`` (durable files); this module
reads them and registers:

- one ``run_observation`` — identity from ``result.json`` content;
- one ``artifact`` per file under ``artifacts/``, identity anchored on
  the sha256 of the file's BYTES (the path is provenance, not identity);
- one ``output`` per non-artifact node output port;
- execution-phase lineage edges run -> artifact and run -> output.

Every payload is a pure function of run-dir CONTENT — no timestamps, no
absolute paths, no machine names — so re-ingesting the same run hashes
to the same version_ids and reuses everything (idempotent, mechanically).

A halted run ingests like any other: a halt is a result, and the
observation registry records what happened, never judges it.

Import cost: stdlib + this package.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os

from .base import AssetError, _check_str, _raise_if
from .lineage import Lineage
from .registry import Registry

__all__ = ["ingest_run"]


def _read_json(path, what) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            obj = json.load(fh)
    except OSError as exc:
        raise AssetError([f"cannot read {what} at {path!r}: {exc}"]) from exc
    except ValueError as exc:
        raise AssetError([f"{what} at {path!r} is not valid JSON: {exc}"]) from exc
    if not isinstance(obj, dict):
        raise AssetError([f"{what} at {path!r} must be a JSON object"])
    return obj


def _file_digest(path) -> str:
    """sha256 of a file's bytes — the artifact's identity anchor."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_artifact_path(value) -> bool:
    """Node output ports holding paths INTO an artifacts dir are backed by
    files the artifacts/ scan already registers — not outputs."""
    return isinstance(value, str) and "artifacts" in value.split(os.sep)


def ingest_run(registry, run_dir, origin="ingest-run") -> dict:
    """Register one completed pipeline run's observations; return a summary.

    Parameters
    ----------
    registry : Registry
        The registry to register into; its model must declare
        ``run_observation``, ``artifact``, and ``output`` (the default
        model does).
    run_dir : str
        A completed run directory (``{name}-{asof}-{hash8}``) containing
        at least ``result.json``.
    origin : str
        Provenance stamp for everything registered here.

    Returns
    -------
    dict
        ``{"run": vid, "artifacts": [vids], "outputs": [vids],
        "edges_added": n}`` — re-ingesting yields the same vids with
        ``edges_added == 0``.

    Examples
    --------
    A sketch, not runnable as-is — it needs a live ``Registry`` and an
    existing run directory under ``pipeline_runs/``::

        summary = ingest_run(reg, "pipeline_runs/my-run-2026-01-01-5ce0d310")
    """
    errors = []
    if not isinstance(registry, Registry):
        errors.append(f"registry must be a Registry, got {type(registry).__name__}")
    _check_str(errors, "run_dir", run_dir)
    _check_str(errors, "origin", origin)
    _raise_if(errors)
    run_dir = os.path.abspath(os.path.expanduser(run_dir))
    if not os.path.isdir(run_dir):
        raise AssetError([f"run_dir does not exist: {run_dir!r}"])

    # -- the run itself, from result.json content only --------------------
    result = _read_json(os.path.join(run_dir, "result.json"), "result.json")
    for key in ("name", "asof", "state", "document_hash", "run_hash"):
        _check_str(errors, f"result.json {key}", result.get(key, ""))
    _raise_if(errors)
    run_name = f"{result['name']}-{result['asof']}-{result['run_hash'][:8]}"
    lineage = Lineage(registry)
    run_vid = registry.register(
        "run_observation",
        {
            "name": run_name,
            "config_hash": result["document_hash"],
            "asof": result["asof"],
            "status": result["state"],
            "summary": {
                "run_hash": result["run_hash"],
                "exit_code": result.get("exit_code"),
                "halted_at": result.get("halted_at", ""),
                "node_states": result.get("node_states", {}),
            },
        },
        origin=origin,
    )

    # -- artifacts: every durable file, identity = digest of its bytes ----
    artifact_vids, edges_added = [], 0
    artifacts_root = os.path.join(run_dir, "artifacts")
    if os.path.isdir(artifacts_root):
        for parent, _dirs, files in sorted(os.walk(artifacts_root)):
            for fname in sorted(files):
                path = os.path.join(parent, fname)
                rel = os.path.relpath(path, artifacts_root)
                payload = {"name": rel, "digest": _file_digest(path)}
                media, _enc = mimetypes.guess_type(fname)
                if media:
                    payload["media_type"] = media
                vid = registry.register(
                    "artifact", payload, refs={"run": run_vid}, origin=origin
                )
                artifact_vids.append(vid)
                edges_added += lineage.add(
                    run_vid, vid, relation="produced", phase="execution", origin=origin
                )

    # -- outputs: every non-artifact node output port ---------------------
    output_vids = []
    nodes_root = os.path.join(run_dir, "nodes")
    if os.path.isdir(nodes_root):
        for fname in sorted(os.listdir(nodes_root)):
            if not fname.endswith(".json"):
                continue
            node = _read_json(os.path.join(nodes_root, fname), f"node record {fname}")
            node_key = node.get("node", "")
            outputs = node.get("outputs", {})
            if not isinstance(node_key, str) or not isinstance(outputs, dict):
                raise AssetError(
                    [f"node record {fname} lacks a valid node/outputs shape"]
                )
            for port, value in sorted(outputs.items()):
                if _is_artifact_path(value):
                    continue
                summary = value if isinstance(value, dict) else {"value": value}
                vid = registry.register(
                    "output",
                    {"name": f"{node_key}.{port}", "summary": summary},
                    refs={"run": run_vid},
                    origin=origin,
                )
                output_vids.append(vid)
                edges_added += lineage.add(
                    run_vid, vid, relation="produced", phase="execution", origin=origin
                )

    return {
        "run": run_vid,
        "artifacts": artifact_vids,
        "outputs": output_vids,
        "edges_added": edges_added,
    }
