"""Fill the provenance a producer left out: code revision, environment, wall time.

A producer that maps some other system's outputs onto events (a replay, a
notebook) rarely knows the git commit it ran at or the interpreter's
packages, and the first real report printed dashes for all of them.
:func:`fill_provenance` runs where the report is rendered inside a run —
the :class:`~dskit.evaluation.nodes.EvaluationReport` node — and fills
ONLY what is missing, recording in ``run_start.sources`` where each filled
fact came from, so a reader can tell a producer's claim from the
renderer's observation.

- ``code``: :func:`git_revision` of the repository containing the run
  directory (``git rev-parse HEAD`` and ``git status --porcelain``);
  absent when git or a repository is absent — never an error.
- ``env``: :meth:`~dskit.evaluation.events.RunStart.capture_env`, which
  reads :class:`dskit.production.release.RuntimeFingerprint` (the one
  owner of runtime capture).
- ``wall_s`` on ``run_end``: now minus the modification time of the run
  directory's ``config.json`` (the driver writes it when the run starts),
  so it covers every node up to the report and excludes the render.

The only other git call in dskit (``pipeline/libs/kronos.py``) VERIFIES a
pinned checkout and raises on drift; this one observes and tolerates
absence, so it is its own small helper rather than a reuse of that one.

Import cost: stdlib plus ``dskit.pipeline`` and ``dskit.production``.
"""

from __future__ import annotations

import json
import os
import subprocess
import time

from dskit.evaluation.events import EvaluationError, RunStart
from dskit.pipeline.runs import CONFIG_FILE, RESOLVED_FILE

__all__ = ["GIT_TIMEOUT_S", "fill_provenance", "git_revision", "run_dir_provenance"]

#: How long a git probe may take before it is treated as absent.
GIT_TIMEOUT_S = 10


def _git(path, *args):
    """Return git's stdout for ``args`` run in ``path``, or None on any failure."""
    try:
        done = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True,
                              timeout=GIT_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def git_revision(path):
    """Return the commit and dirty flag of the repository containing ``path``.

    Parameters
    ----------
    path : str
        Any directory inside the working tree.

    Returns
    -------
    dict or None
        ``{"commit": str, "dirty": bool}``, or None when ``git`` is not
        installed or ``path`` is not inside a repository.

    Examples
    --------
    ::

        git_revision(".")  # {'commit': '4844e5f...', 'dirty': False}
    """
    if not path or not os.path.isdir(path):
        return None
    commit = _git(path, "rev-parse", "HEAD")
    if not commit or not commit.strip():
        return None
    status = _git(path, "status", "--porcelain", "--untracked-files=no")
    return {"commit": commit.strip(), "dirty": None if status is None else bool(status.strip())}


def fill_provenance(events, run_dir, now=None):
    """Return ``events`` with missing ``code``, ``env`` and ``wall_s`` filled.

    Only the first event (``run_start``) and a final ``run_end`` are
    touched, and only for fields they lack; each fill is named in
    ``run_start.sources``. The input list and its dicts are not mutated.

    Parameters
    ----------
    events : list of dict
        Schema-v1 event objects in log order.
    run_dir : str or None
        The run directory; its repository and ``config.json`` are read.
    now : float or None
        Epoch seconds for the wall-time reading (tests pin it).

    Returns
    -------
    list of dict

    Examples
    --------
    ::

        filled = fill_provenance(events, ctx.run_dir)
        filled[0]["sources"]["code"]  # 'git -C <run_dir> (filled by the report)'
    """
    events = list(events)
    if not events or not isinstance(events[0], dict) or events[0].get("kind") != "run_start":
        return events
    start = dict(events[0])
    sources = dict(start.get("sources", {}))
    if not start.get("code") and run_dir:
        revision = git_revision(run_dir)
        if revision is not None:
            start["code"] = revision
            sources["code"] = "git rev-parse HEAD / status in the run directory's repository, " \
                              "read by the report node"
    if not start.get("env"):
        start["env"] = RunStart.capture_env()
        sources["env"] = "RuntimeFingerprint of the interpreter that rendered the report"
    last = events[-1]
    config = os.path.join(run_dir, CONFIG_FILE) if run_dir else None
    if (isinstance(last, dict) and last.get("kind") == "run_end" and "wall_s" not in last
            and config and os.path.isfile(config)):
        wall = (time.time() if now is None else now) - os.path.getmtime(config)
        if wall >= 0:
            events[-1] = {**last, "wall_s": round(wall, 3)}
            sources["wall_s"] = f"report node: now minus {CONFIG_FILE} mtime (run start); " \
                                "excludes rendering"
    if sources:
        start["sources"] = sources
    events[0] = start
    return events


def _read_json(run_dir, name):
    """Return the JSON object in ``run_dir/name``, or None when the file is absent."""
    path = os.path.join(run_dir, name)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        try:
            return json.load(fh)
        except json.JSONDecodeError as exc:
            raise EvaluationError([f"{path} is not valid JSON: {exc}"]) from None


def run_dir_provenance(run_dir):
    """Return the ``run_start`` facts a pipeline run directory records.

    ``run_id`` is the driver's ``run_hash``, ``config_hash`` its
    ``document_hash`` and ``data`` its ``data_fingerprint`` (all from
    ``resolved.json``); ``config`` is the run's ``config.json``. Only the
    facts present are returned, so an event producer merges them over its
    own defaults without inventing any.

    Parameters
    ----------
    run_dir : str or None
        The run directory (``NodeContext.run_dir``); None returns ``{}``.

    Returns
    -------
    dict
        A subset of ``run_id``, ``config_hash``, ``data``, ``config``.
    """
    if not run_dir:
        return {}
    resolved = _read_json(run_dir, RESOLVED_FILE) or {}
    config = _read_json(run_dir, CONFIG_FILE)
    out = {}
    if resolved.get("run_hash"):
        out["run_id"] = resolved["run_hash"]
    if resolved.get("document_hash"):
        out["config_hash"] = resolved["document_hash"]
    if isinstance(resolved.get("data_fingerprint"), dict):
        out["data"] = resolved["data_fingerprint"]
    if isinstance(config, dict):
        out["config"] = config
    return out
