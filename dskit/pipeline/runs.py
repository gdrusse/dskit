"""Cross-run comparison: reading back the run directories a driver left.

`driver.run_document` writes one directory per run —
``{name}-{asof}-{run_hash[:8]}/`` holding ``config.json`` (the document as
declared), ``resolved.json``, ``result.json`` (the machine verdict),
``carry.json``, per-node records under ``nodes/``, and a human
``report.md``. Nothing read anything BACK: comparing two runs meant
opening two ``report.md`` files by hand.

This module is the reader. It is deliberately structural — every value it
reports comes from a JSON record (``result.json`` for identity and state,
``nodes/NN-*.json`` plus ``carry.json`` for metrics, ``config.json`` for
declared params) and never from ``report.md``, whose prose is written for
a human and is free to change wording. A run directory missing its
report still tabulates.

Two records are needed for metrics because the writer splits them: a
node's top-level numeric outputs survive into its record, but a `metrics`
DICT is summarized there as ``{"type": "dict", "len": n}`` and its numbers
survive only in ``carry.json``. The reader overlays the two and applies
the numeric-leaf rule :func:`node_metrics` — the very function the driver
applies when feeding its tracking sinks — to the result. Where the
overlay fails (a metrics dict too large or non-finite to carry), what
remains is that SUMMARY MARKER, and the marker is a note about a
measurement, not a measurement: mining its ``len`` would report a dict's
key count as a number someone could plot. The reader drops it and says so
(:attr:`RunSummary.notes`).

Tier 1: stdlib only, no knowledge of any domain.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from dskit.pipeline.markdown import pipe_table

__all__ = [
    "DEFAULT_RUN_ROOT",
    "RunProblem",
    "RunSummary",
    "format_runs",
    "node_metrics",
    "param_at",
    "resolve_run_root",
    "scan_runs",
]

#: Where runs land when a document declares no ``outputs.run_root`` — the
#: ONE name for that default. The driver resolves its own write path
#: through :func:`resolve_run_root` rather than restating the literal.
DEFAULT_RUN_ROOT = "./pipeline_runs"

#: The files a run directory is read through. `report.md` is absent on
#: purpose — prose is never a data source.
RESULT_FILE = "result.json"
CONFIG_FILE = "config.json"
CARRY_FILE = "carry.json"
NODES_DIR = "nodes"

#: Keys `result.json` must carry for a directory to count as a run.
_REQUIRED = ("name", "asof", "run_hash", "state")

#: The fixed left-hand columns of the table, before params and metrics.
_FIXED_COLUMNS = ("name", "asof", "state", "run", "doc")


def resolve_run_root(declared):
    """Resolve what a document's ``run_root`` declaration means on disk.

    Parameters
    ----------
    declared : str or None
        ``outputs.run_root`` as declared; empty or None means
        :data:`DEFAULT_RUN_ROOT`.

    Returns
    -------
    str
        The absolute path, with ``~`` expanded. Both the driver's
        per-run writer and its walk-forward writer resolve through here,
        so the default cannot move in one and not the other.
    """
    return os.path.abspath(os.path.expanduser(declared or DEFAULT_RUN_ROOT))


def node_metrics(outputs):
    """Extract the numeric view of one node's outputs.

    Top-level numeric scalars, plus every numeric leaf of an output
    literally named ``metrics`` — never bulk payloads, and never
    booleans (``True`` is a verdict, not a measurement).

    This is THE rule, one name: ``driver._node_metrics`` is this
    function, applied to live outputs on the writing side. It sees real
    outputs only — the reader strips the writer's summary markers before
    calling it (see :func:`_run_metrics`), so a node whose metrics are a
    literal ``{"type": ..., "len": ...}`` mapping still measures.

    Parameters
    ----------
    outputs : dict
        One node's outputs, ``{name: value}``, as recorded or as carried.

    Returns
    -------
    dict
        ``{metric name: float or int}``; ``metrics.<key>`` for the
        nested leaves. Empty when the node measured nothing.
    """
    out = {}
    for key, value in outputs.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out[key] = value
        elif key == "metrics" and isinstance(value, dict):
            for mk, mv in value.items():
                if isinstance(mv, (int, float)) and not isinstance(mv, bool):
                    out[f"metrics.{mk}"] = mv
    return out


def param_at(config, path):
    """One declared value out of a run's ``config.json``, by dotted path.

    Parameters
    ----------
    config : dict
        The document as declared (``config.json``).
    path : str
        Dotted path, e.g. ``"pipeline.qhat.params.epochs"`` or ``"name"``.

    Returns
    -------
    object or None
        The value, or None where the path does not exist — an absent knob
        is a legitimate answer when comparing runs of different documents,
        not an error.
    """
    node = config
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


@dataclass(frozen=True)
class RunProblem:
    """One directory entry the scan could not read as a run.

    Reported rather than dropped: a run root shared with foreign
    directories must say what it ignored, or a missing run looks like a
    run that never happened.

    Parameters
    ----------
    entry : str
        The entry's name inside the run root.
    reason : str
        Why it is not tabulated, in the operator's words.

    Examples
    --------
    Build one directly::

        problem = RunProblem(entry="notes", reason="not a directory")
    """

    entry: str
    reason: str


@dataclass(frozen=True)
class RunSummary:
    """One run, as its own records describe it.

    Parameters
    ----------
    run_dir : str
        Absolute path to the run directory.
    name : str
        The document's name — the run series this run belongs to.
    asof : str
        The run's asof, ``YYYY-MM-DD``.
    state : str
        ``"ran"`` | ``"halted"`` | ``"error"``, from ``result.json``.
    run_hash : str
        Identity of what was computed (document identity + data
        fingerprints); its first 8 characters name the directory.
    document_hash : str
        Identity of the document alone.
    metrics : dict
        ``{"<node>.<metric>": number}`` across every node of the run.
    config : dict
        The document as declared, for :func:`param_at`.
    mtime : float
        Directory mtime — the recency tiebreak between same-asof reruns.
    notes : tuple of str, optional
        What the run measured but this reader could not recover — a
        metrics dict the writer summarized and could not carry. A blank
        column and a missing measurement look identical, so the
        difference is stated.

    Examples
    --------
    Summaries normally come from :func:`scan_runs`, but the class is a
    plain record and builds directly::

        run = RunSummary(
            run_dir="/tmp/pipeline_runs/demo-2026-01-01-0badc0de",
            name="demo",
            asof="2026-01-01",
            state="ran",
            run_hash="0badc0de" * 8,
            document_hash="feedface" * 8,
            metrics={"score.metrics.loss": 0.25},
            config={"name": "demo"},
            mtime=0.0,
        )
    """

    run_dir: str
    name: str
    asof: str
    state: str
    run_hash: str
    document_hash: str
    metrics: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)
    mtime: float = 0.0
    notes: tuple = ()


def scan_runs(root=None):
    """Read every run directory under a run root, newest first.

    Parameters
    ----------
    root : str, optional
        The run root; defaults to :data:`DEFAULT_RUN_ROOT`, the same
        default the driver writes to. ``~`` is expanded.

    Returns
    -------
    tuple
        ``(runs, problems)`` — a tuple of :class:`RunSummary` ordered by
        asof then mtime, DESCENDING (the ordering the driver uses to find
        a series' previous run, reversed), and a tuple of
        :class:`RunProblem` for every entry that was not a run.

    Raises
    ------
    OSError
        The run root does not exist — an empty root is a legitimate "no
        runs yet", a missing one is a wrong path.
    """
    root = resolve_run_root(root)
    if not os.path.isdir(root):
        raise NotADirectoryError(f"no run root at {root}")
    runs, problems = [], []
    for entry in sorted(os.listdir(root)):
        summary, reason = _read_run(os.path.join(root, entry))
        if summary is None:
            problems.append(RunProblem(entry=entry, reason=reason))
        else:
            runs.append(summary)
    runs.sort(key=lambda r: (r.asof, r.mtime), reverse=True)
    return tuple(runs), tuple(problems)


def _read_run(run_dir):
    """One directory as a RunSummary, or ``(None, reason)``."""
    if not os.path.isdir(run_dir):
        return None, "not a directory"
    result, reason = _load_json(os.path.join(run_dir, RESULT_FILE))
    if result is None:
        return None, reason
    if not isinstance(result, dict):
        return None, f"{RESULT_FILE} is not an object"
    absent = [key for key in _REQUIRED if key not in result]
    if absent:
        return None, f"{RESULT_FILE} is missing {', '.join(absent)}"
    config, _ = _load_json(os.path.join(run_dir, CONFIG_FILE))
    metrics, notes = _run_metrics(run_dir)
    return (
        RunSummary(
            run_dir=run_dir,
            name=str(result["name"]),
            asof=str(result["asof"]),
            state=str(result["state"]),
            run_hash=str(result["run_hash"]),
            document_hash=str(result.get("document_hash", "")),
            metrics=metrics,
            config=config if isinstance(config, dict) else {},
            mtime=os.path.getmtime(run_dir),
            notes=notes,
        ),
        "",
    )


def _load_json(path):
    """``(payload, "")`` or ``(None, reason)`` — never raises."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh), ""
    except FileNotFoundError:
        return None, f"no {os.path.basename(path)}"
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"unreadable {os.path.basename(path)}: {exc}"


def _is_summary_marker(value):
    """Report whether a value is ``driver._summarize``'s container marker."""
    return (
        isinstance(value, dict)
        and set(value) == {"type", "len"}
        and isinstance(value["type"], str)
        and isinstance(value["len"], int)
        and not isinstance(value["len"], bool)
    )


def _run_metrics(run_dir):
    """Collect ``(metrics, notes)`` for one run directory.

    Every node's numeric outputs, keyed ``<node>.<metric>``, plus a note
    per measurement the records could not give back.
    """
    carry, _ = _load_json(os.path.join(run_dir, CARRY_FILE))
    carried = carry if isinstance(carry, dict) else {}
    metrics, notes = {}, []
    for key, outputs in _node_outputs(run_dir).items():
        merged = {k: v for k, v in outputs.items() if not _is_summary_marker(v)}
        node_carry = carried.get(key)
        if isinstance(node_carry, dict):
            merged.update(node_carry)
        recorded = outputs.get("metrics")
        if _is_summary_marker(recorded) and recorded["type"] == "dict":
            if "metrics" not in merged:
                notes.append(
                    f"{key}: its metrics dict was summarized in the record "
                    "and too large or non-finite to carry — not tabulated"
                )
        for metric, value in node_metrics(merged).items():
            metrics[f"{key}.{metric}"] = value
    return metrics, tuple(notes)


def _node_outputs(run_dir):
    """``{node key: recorded outputs}`` from ``nodes/NN-<key>.json``."""
    nodes_dir = os.path.join(run_dir, NODES_DIR)
    if not os.path.isdir(nodes_dir):
        return {}
    out = {}
    for entry in sorted(os.listdir(nodes_dir)):
        record, _ = _load_json(os.path.join(nodes_dir, entry))
        if not isinstance(record, dict):
            continue
        key, outputs = record.get("node"), record.get("outputs")
        if isinstance(key, str) and isinstance(outputs, dict):
            out[key] = outputs
    return out


def format_runs(runs, metrics=(), params=()):
    """Tabulate scanned runs as a markdown pipe table.

    Parameters
    ----------
    runs : sequence of RunSummary
        The runs to show, in the order they should appear.
    metrics : sequence of str, optional
        Metric columns, in order. Default: every metric any run reported,
        sorted — showing all of them is what makes an unexpected column
        visible.
    params : sequence of str, optional
        Dotted ``config.json`` paths to add as columns (:func:`param_at`).

    Returns
    -------
    str
        The table, rendered by :func:`~dskit.pipeline.markdown.pipe_table`
        so a value reads the same here as in a run report, or a single
        ``no runs`` line when there is nothing to show — an empty table
        would read as a rendering failure.
    """
    runs = tuple(runs)
    if not runs:
        return "no runs found"
    metric_columns = tuple(metrics) or _all_metrics(runs)
    columns = (*_FIXED_COLUMNS, *params, *metric_columns)
    rows = [
        (
            run.name,
            run.asof,
            run.state,
            run.run_hash[:8],
            run.document_hash[:8],
            *(param_at(run.config, path) for path in params),
            *(run.metrics.get(key) for key in metric_columns),
        )
        for run in runs
    ]
    return "\n".join(pipe_table(columns, rows))


def _all_metrics(runs):
    """Every metric key any run reported, sorted."""
    return tuple(sorted({key for run in runs for key in run.metrics}))
