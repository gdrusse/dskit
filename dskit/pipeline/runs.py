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
the same numeric-leaf rule the driver applies when feeding its tracking
sinks (see :func:`node_metrics`).

Tier 1: stdlib only, no knowledge of any domain.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

__all__ = [
    "DEFAULT_RUN_ROOT",
    "RunProblem",
    "RunSummary",
    "format_runs",
    "node_metrics",
    "param_at",
    "scan_runs",
]

#: Where runs land when a document declares no ``outputs.run_root``. The
#: driver holds the same default; ``test_runs.py`` pins the agreement by
#: running a rootless document and reading it back from here.
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

_MISSING = "—"


def node_metrics(outputs) -> dict:
    """Extract the numeric view of one node's outputs.

    Top-level numeric scalars, plus every numeric leaf of an output
    literally named ``metrics`` — never bulk payloads, and never
    booleans (``True`` is a verdict, not a measurement).

    This restates ``driver._node_metrics``, which applies the same rule
    to LIVE outputs on the writing side. The two are held together by a
    pin (``TestMetricRulePin``) rather than shared, because ``driver.py``
    is a pre-standard module whose docstring conversion is owed its own
    commit.

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

    @property
    def dir_name(self) -> str:
        """The run directory's own name, the label an operator recognises.

        Returns
        -------
        str
            The basename of ``run_dir``.
        """
        return os.path.basename(self.run_dir)


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
    root = os.path.abspath(os.path.expanduser(root or DEFAULT_RUN_ROOT))
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
    return (
        RunSummary(
            run_dir=run_dir,
            name=str(result["name"]),
            asof=str(result["asof"]),
            state=str(result["state"]),
            run_hash=str(result["run_hash"]),
            document_hash=str(result.get("document_hash", "")),
            metrics=_run_metrics(run_dir),
            config=config if isinstance(config, dict) else {},
            mtime=os.path.getmtime(run_dir),
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


def _run_metrics(run_dir) -> dict:
    """Every node's numeric outputs, keyed ``<node>.<metric>``."""
    carry, _ = _load_json(os.path.join(run_dir, CARRY_FILE))
    carried = carry if isinstance(carry, dict) else {}
    metrics = {}
    for key, outputs in _node_outputs(run_dir).items():
        merged = dict(outputs)
        node_carry = carried.get(key)
        if isinstance(node_carry, dict):
            merged.update(node_carry)
        for metric, value in node_metrics(merged).items():
            metrics[f"{key}.{metric}"] = value
    return metrics


def _node_outputs(run_dir) -> dict:
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


def format_runs(runs, metrics=(), params=()) -> str:
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
        The table, or a single ``no runs`` line when there is nothing to
        show — an empty table would read as a rendering failure.
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
            *(_cell(param_at(run.config, path)) for path in params),
            *(_cell(run.metrics.get(key)) for key in metric_columns),
        )
        for run in runs
    ]
    lines = [
        "| " + " | ".join(columns) + " |",
        "|" + "---|" * len(columns),
    ]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def _all_metrics(runs) -> tuple:
    """Every metric key any run reported, sorted."""
    return tuple(sorted({key for run in runs for key in run.metrics}))


def _cell(value) -> str:
    """One table cell: absent reads as a dash, never as an empty column."""
    if value is None:
        return _MISSING
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True, default=str)
    return str(value)
