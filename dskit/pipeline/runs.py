"""Cross-run comparison: reading back the run directories a driver left.

``driver.run_document`` writes one directory per run; nothing read them
BACK — comparing runs meant opening ``report.md`` files by hand. This
module is the reader, and it is deliberately structural: every value
comes from a JSON record (``result.json``, ``nodes/NN-*.json`` plus
``carry.json``, ``config.json``), never from ``report.md``, whose prose
is written for a human and free to change wording.

Metrics overlay two records because the writer splits them: a `metrics`
DICT is summarized out of the node record as ``{"type": "dict", "len":
n}`` and its numbers survive only in ``carry.json``. The reader drops
the bare marker (its ``len`` is a key count, not a measurement) and
NOTES every number it could not recover (:attr:`RunSummary.notes`) — a
blank cell must mean "never measured", nothing else, and the verb prints
every note.

Tier 1: stdlib only, no knowledge of any domain.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

__all__ = [
    "CARRY_FILE",
    "CONFIG_FILE",
    "DEFAULT_RUN_ROOT",
    "NODES_DIR",
    "RESULT_FILE",
    "RunProblem",
    "RunSummary",
    "format_runs",
    "node_metrics",
    "param_at",
    "resolve_run_root",
    "scan_runs",
    "unknown_metrics",
    "unknown_params",
]

#: Where runs land when a document declares no ``outputs.run_root`` —
#: the reader's name for the default the driver writes to; the agreement
#: is pinned in tests/pipeline/test_runs.py (TestScan).
DEFAULT_RUN_ROOT = "./pipeline_runs"

#: The run-dir layout, as the reader names it. `driver.py` and
#: `dskit/assets/ingest.py` restate the names, so the agreement is
#: PINNED in tests/pipeline/test_runs.py::TestRunDirLayout. `report.md`
#: is absent on purpose — prose is never a data source.
RESULT_FILE = "result.json"
CONFIG_FILE = "config.json"
CARRY_FILE = "carry.json"
NODES_DIR = "nodes"

#: Keys `result.json` must carry AS NON-EMPTY STRINGS for a directory to
#: count as a run. Presence alone is not enough: an empty run_hash would
#: render the MISSING dash in an identity cell — which reads as a
#: rendering bug rather than as the unreadable directory it is — and a
#: null document_hash would render the literal ``None``, a fabricated
#: "hash prefix". The other run-dir reader, `dskit.assets.ingest`,
#: requires the same five the same way (`_check_str`); the agreement is
#: pinned in tests/pipeline/test_runs.py::TestRunDirLayout.
_REQUIRED = ("name", "asof", "run_hash", "state", "document_hash")

#: What `driver._summarize` leaves behind for a non-finite float: `inf`
#: is not JSON, so the record keeps `repr(value)` — text, not a number.
_NON_FINITE = ("inf", "-inf", "nan")

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
        The absolute path, with ``~`` expanded — the same resolution the
        driver applies to its write path (the agreement is pinned by the
        default-root test in tests/pipeline/test_runs.py).
    """
    return os.path.abspath(os.path.expanduser(declared or DEFAULT_RUN_ROOT))


def node_metrics(outputs):
    """Extract the numeric view of one node's outputs.

    Top-level numeric scalars, plus the numeric values sitting DIRECTLY
    inside an output literally named ``metrics`` — one level, never
    recursed: a dict nested inside ``metrics`` is a payload, and its
    numbers reach neither the tracking sinks nor the runs table. Never
    booleans (``True`` is a verdict, not a measurement).

    The same rule the writer applies when feeding its tracking sinks
    (``driver._node_metrics``); the two copies are pinned case for case
    by ``tests/pipeline/test_runs.py::TestMetricRulePin``. This side sees
    real outputs only — the reader strips the writer's summary markers
    before calling it (see :func:`_run_metrics`), so a node whose metrics
    are a literal ``{"type": ..., "len": ...}`` mapping still measures.

    Parameters
    ----------
    outputs : dict
        One node's outputs, ``{name: value}``, as recorded or as carried.

    Returns
    -------
    dict
        ``{metric name: float or int}``; ``metrics.<key>`` for the
        ``metrics`` dict's direct numeric values. Empty when the node
        measured nothing.
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
        not an error. (A path absent from EVERY scanned run's config is a
        typo instead, and :func:`unknown_params` exists to refuse it.)
    """
    return _walk(config, path)[1]


def _walk(config, path):
    """Walk a dotted path in a config mapping: ``(declared, value)``.

    The one walker under both :func:`param_at` (which wants the value)
    and :func:`unknown_params` (which wants the DISTINCTION between a
    knob declared null and a path never declared — a distinction the
    value alone cannot carry).
    """
    node = config
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


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
        metrics dict the writer summarized and could not carry, a
        non-finite scalar recorded as text, a node record or a
        ``config.json`` that would not parse. A blank column and a
        missing measurement look identical, so the difference is stated.

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
    if not isinstance(result, dict):
        # A file holding `null` parses to None with no reason of its own,
        # and a skip with a blank reason is a silent skip.
        return None, reason or f"{RESULT_FILE} is not an object"
    bad = [
        key
        for key in _REQUIRED
        if not (isinstance(result.get(key), str) and result[key])
    ]
    if bad:
        return None, f"{RESULT_FILE} is missing a usable {', '.join(bad)}"
    config, config_reason = _load_json(os.path.join(run_dir, CONFIG_FILE))
    metrics, notes = _run_metrics(run_dir)
    if not isinstance(config, dict):
        reason = config_reason or f"{CONFIG_FILE} is not an object"
        notes = (f"{reason} — no declared param is shown for this run", *notes)
    return (
        RunSummary(
            run_dir=run_dir,
            name=str(result["name"]),
            asof=str(result["asof"]),
            state=str(result["state"]),
            run_hash=str(result["run_hash"]),
            document_hash=str(result["document_hash"]),
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
    per measurement the records could not give back. The two records are
    read INDEPENDENTLY and unioned: a node whose record is truncated
    still contributes whatever ``carry.json`` holds for it, because a
    number that survived on disk must not be lost to a broken file
    beside it.
    """
    carry, carry_reason = _load_json(os.path.join(run_dir, CARRY_FILE))
    carried = carry if isinstance(carry, dict) else {}
    notes = []
    if not isinstance(carry, dict):
        reason = carry_reason or f"{CARRY_FILE} is not an object"
        notes.append(f"{reason} — carried metrics are not tabulated")
    recorded, record_notes = _node_outputs(run_dir)
    notes.extend(record_notes)
    metrics = {}
    for key in sorted(set(recorded) | set(carried)):
        outputs = recorded.get(key, {})
        merged = {k: v for k, v in outputs.items() if not _is_summary_marker(v)}
        node_carry = carried.get(key)
        if isinstance(node_carry, dict):
            merged.update(node_carry)
        notes.extend(_unrecovered(key, outputs, merged))
        for metric, value in node_metrics(merged).items():
            metrics[f"{key}.{metric}"] = value
    return metrics, tuple(notes)


def _unrecovered(key, outputs, merged):
    """Name one node's measurements that survived as text, not as data.

    Two shapes exist, both from ``driver._summarize``: a `metrics` DICT
    reduced to the container marker, and a non-finite float reduced to
    its ``repr``. Either renders the same blank cell as a node that
    measured nothing, and only the latter is routine.
    """
    notes = []
    marker = outputs.get("metrics")
    if (
        _is_summary_marker(marker)
        and marker["type"] == "dict"
        and "metrics" not in merged
    ):
        notes.append(
            f"{key}: its metrics dict was summarized in the record "
            "and too large or non-finite to carry — not tabulated"
        )
    for name, value in outputs.items():
        recovered = merged.get(name)
        if value in _NON_FINITE and not isinstance(recovered, (int, float)):
            notes.append(
                f"{key}.{name}: measured {value} — non-finite, recorded as "
                "text and not tabulated as a number"
            )
    return notes


def _node_outputs(run_dir):
    """``({node key: recorded outputs}, notes)`` from ``nodes/NN-*.json``.

    Every record that cannot be read is NAMED: a node dropped in silence
    is a column that quietly goes blank.
    """
    nodes_dir = os.path.join(run_dir, NODES_DIR)
    if not os.path.isdir(nodes_dir):
        return {}, [f"no {NODES_DIR}/ — per-node records are not tabulated"]
    out, notes = {}, []
    for entry in sorted(os.listdir(nodes_dir)):
        record, reason = _load_json(os.path.join(nodes_dir, entry))
        if not isinstance(record, dict):
            notes.append(
                f"{NODES_DIR}/{entry}: "
                f"{reason or 'is not an object'} — not tabulated"
            )
            continue
        key, outputs = record.get("node"), record.get("outputs")
        if isinstance(key, str) and isinstance(outputs, dict):
            out[key] = outputs
        else:
            notes.append(
                f"{NODES_DIR}/{entry}: no node/outputs pair — not tabulated"
            )
    return out, notes


#: What an absent value reads as — never the empty string: a blank cell
#: is indistinguishable from a rendering bug.
_MISSING = "—"

#: Longest cell rendered in full; beyond this a table stops being a table.
_MAX_CELL = 120


def _render_cell(value):
    """One cell as trustworthy text: absent reads as the dash, booleans as a verdict, floats to 6 s.f., containers as their size; ``|`` is escaped and line breaks flatten to ``⏎`` so a value cannot open a phantom column or row."""
    if value is None:
        return _MISSING
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, (list, tuple, set, frozenset)):
        text = f"({len(value)} item(s))"
    elif isinstance(value, dict):
        text = f"({len(value)} key(s))"
    else:
        text = str(value)
    # Flatten BEFORE the emptiness check: a line-break-only value must
    # read as MISSING, not as the blank cell declared impossible above.
    text = "⏎".join(text.splitlines())
    if not text:
        return _MISSING
    if len(text) > _MAX_CELL:
        text = text[:_MAX_CELL] + "…"
    return text.replace("|", r"\|")


def _render_table(columns, rows):
    """Header, separator, one line per row — order preserved, every cell through :func:`_render_cell`."""
    header = [_render_cell(column) for column in columns]
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "---|" * len(header),
    ]
    lines += [
        "| " + " | ".join(_render_cell(value) for value in row) + " |"
        for row in rows
    ]
    return lines


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
        The table, cells escaped (a ``|`` or a line break in a value
        cannot shift a column or split a row), or a single ``no runs``
        line when there is nothing to show — an empty table would read
        as a rendering failure.
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
    return "\n".join(_render_table(columns, rows))


def unknown_metrics(runs, metrics):
    """Name the requested metric keys that NO scanned run ever reported.

    A metric column exists only because some run measured it — there is
    no "declared but unmeasured" concept for metrics (unlike a declared
    param, where :func:`param_at` documents None as a legitimate answer).
    A key absent from every run is therefore a typo, and rendering it as
    a full column of blanks would read as "these runs never measured it"
    — a confidently wrong table. The verb refuses instead.

    Parameters
    ----------
    runs : sequence of RunSummary
        Every scanned run — not the ``--limit``'d view: a metric only an
        older run measured is a real key, and blanks over the shown runs
        are then a true statement.
    metrics : sequence of str
        The requested metric column keys.

    Returns
    -------
    tuple of str
        The keys of ``metrics`` no run in ``runs`` reported, in request
        order; empty when every key is real.
    """
    known = set(_all_metrics(runs))
    return tuple(key for key in metrics if key not in known)


def unknown_params(runs, params):
    """Name the requested param paths NO scanned run's config declares.

    :func:`param_at` documents None as a legitimate answer — for a knob
    a PARTICULAR document never declared while others did. A path no
    scanned run declares at all is not that: it is a typo, and a full
    column of blanks would read as "these documents do not set it" — the
    same confidently wrong table :func:`unknown_metrics` refuses for
    metrics. A knob declared with a null value counts as declared.

    Parameters
    ----------
    runs : sequence of RunSummary
        Every scanned run — not the ``--limit``'d view, for the same
        reason as :func:`unknown_metrics`.
    params : sequence of str
        The requested dotted ``config.json`` paths.

    Returns
    -------
    tuple of str
        The paths of ``params`` no run's config declares, in request
        order; empty when every path is real.
    """
    return tuple(
        path
        for path in params
        if not any(_walk(run.config, path)[0] for run in runs)
    )


def _all_metrics(runs):
    """Every metric key any run reported, sorted."""
    return tuple(sorted({key for run in runs for key in run.metrics}))
