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

from dskit.pipeline.records import number_ok

__all__ = [
    "ARTIFACTS_DIR",
    "CARRY_FILE",
    "CONFIG_FILE",
    "DEFAULT_RUN_ROOT",
    "NODES_DIR",
    "RESULT_FILE",
    "RunProblem",
    "RunSummary",
    "SKILL_FILE",
    "WALKFORWARD_FILE",
    "format_runs",
    "format_skill",
    "node_metrics",
    "param_at",
    "read_curve_records",
    "read_skill_series",
    "resolve_run_root",
    "scan_runs",
    "score_walk",
    "unknown_metrics",
    "unknown_params",
    "walk_fold_dirs",
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
ARTIFACTS_DIR = "artifacts"

#: The per-row loss gaps ADR-0067 pools, written by a score node
#: through ``Node.write_artifact`` and read back by
#: :func:`score_walk`. It lives under ``artifacts/<node>/`` because
#: ``carry.json`` is run-over-run STATE with a 20 kB ceiling, and one
#: fold's gaps are two orders of magnitude past it.
SKILL_FILE = "skill.json"

#: The machine record `driver.run_walk_forward` writes into its summary
#: dir (beside `report.md`; deliberately no `result.json`). The scan
#: uses it to name the driver's own summary dirs for what they are in
#: the skipped list, instead of listing them as foreign strays.
WALKFORWARD_FILE = "walkforward.json"

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
        if os.path.isfile(os.path.join(run_dir, WALKFORWARD_FILE)):
            # The driver's own walk-forward summary layout — its record
            # beside report.md, deliberately no result.json. Not foreign:
            # named for what it is, so it cannot drown the real strays.
            return None, "walk-forward summary (not a run)"
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
    """``(payload, "")`` or ``(None, reason)`` — never raises: ValueError covers JSONDecodeError AND the UnicodeDecodeError binary bytes raise."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh), ""
    except FileNotFoundError:
        return None, f"no {os.path.basename(path)}"
    except (OSError, ValueError) as exc:
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
    carry_ok = isinstance(carry, dict)
    carried = carry if carry_ok else {}
    notes = []
    if not carry_ok:
        reason = carry_reason or f"{CARRY_FILE} is not an object"
        notes.append(f"{reason} — carried metrics are not tabulated")
    recorded, record_notes = _node_outputs(run_dir)
    notes.extend(record_notes)
    metrics = {}
    for key in sorted(set(recorded) | set(carried)):
        outputs = recorded.get(key, {})
        merged = {k: v for k, v in outputs.items() if not _is_summary_marker(v)}
        node_carry = carried.get(key)
        node_carry = node_carry if isinstance(node_carry, dict) else {}
        merged.update(node_carry)
        if carry_ok:
            # Both diagnoses read the node's CARRY as evidence; with
            # carry.json unreadable, its own note is the whole story —
            # a per-node verdict beside it would contradict it.
            notes.extend(_unrecovered(key, outputs, node_carry))
        for metric, value in node_metrics(merged).items():
            metrics[f"{key}.{metric}"] = value
    return metrics, tuple(notes)


def _unrecovered(key, outputs, carried):
    """Name one node's measurements that survived as text, not as data.

    Both diagnoses are judged against the node's CARRY (the caller skips
    this entirely when ``carry.json`` was unreadable). ``_carryable``
    refuses non-finite floats, so a `metrics` dict absent from carry was
    too large or non-finite to keep, and a top-level ``"inf"``/``"nan"``
    absent from carry is a diverged float's ``repr`` — while the same
    text PRESENT in carry is provably a genuine string, and no note
    fires: a string simply isn't a numeric metric.
    """
    notes = []
    marker = outputs.get("metrics")
    if (
        _is_summary_marker(marker)
        and marker["type"] == "dict"
        and "metrics" not in carried
    ):
        notes.append(
            f"{key}: its metrics dict was summarized in the record "
            "and too large or non-finite to carry — not tabulated"
        )
    for name, value in outputs.items():
        if value in _NON_FINITE and name not in carried:
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
    try:
        entries = sorted(os.listdir(nodes_dir))
    except OSError as exc:
        # Existing but unlistable (permissions, I/O): name it on this
        # run and let the scan go on — one refusing directory must not
        # take the whole table down.
        return {}, [
            f"unlistable {NODES_DIR}/: {exc} — per-node records are not tabulated"
        ]
    out, notes = {}, []
    for entry in entries:
        record, reason = _load_json(os.path.join(nodes_dir, entry))
        if not isinstance(record, dict):
            notes.append(
                f"{NODES_DIR}/{entry}: {reason or 'is not an object'} — not tabulated"
            )
            continue
        key, outputs = record.get("node"), record.get("outputs")
        if isinstance(key, str) and isinstance(outputs, dict):
            out[key] = outputs
        else:
            notes.append(f"{NODES_DIR}/{entry}: no node/outputs pair — not tabulated")
    return out, notes


#: What an absent value reads as — never the empty string: a blank cell
#: is indistinguishable from a rendering bug.
_MISSING = "—"

#: Longest cell rendered in full; beyond this a table stops being a table.
_MAX_CELL = 120


def _escape_pipe(text):
    """Escape every markdown pipe in ``text``: a raw ``|`` ENDS a cell, so an unescaped one hands the row a phantom column — `driver._md_cell` takes the rule from here rather than restating it, because a table's FORMAT is taste and its ESCAPING is correctness."""
    return text.replace("|", r"\|")


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
    return _escape_pipe(text)


def _render_table(columns, rows):
    """Header, separator, one line per row — order preserved, every cell through :func:`_render_cell`."""
    header = [_render_cell(column) for column in columns]
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "---|" * len(header),
    ]
    lines += [
        "| " + " | ".join(_render_cell(value) for value in row) + " |" for row in rows
    ]
    return lines


def walk_fold_dirs(summary_dir):
    """Name the fold run dirs a walk-forward summary lists, in order.

    Only folds whose ``state`` is ``ran`` — a skipped or failed fold has
    no run dir to read, and silently treating it as a gap would let a
    short walk pass as a long one.

    Parameters
    ----------
    summary_dir : str
        A walk-forward summary directory (the one holding
        :data:`WALKFORWARD_FILE`).

    Returns
    -------
    list of str
        Absolute fold run directories, in the order the walk ran them.

    Raises
    ------
    ValueError
        When the directory holds no readable walk-forward record.

    Examples
    --------
    The count is the walk's fold count::

        len(walk_fold_dirs(summary))  # 20
    """
    record, _why = _load_json(os.path.join(summary_dir, WALKFORWARD_FILE))
    if not isinstance(record, dict) or not isinstance(record.get("folds"), list):
        raise ValueError(
            f"{summary_dir} holds no readable {WALKFORWARD_FILE} — "
            "not a walk-forward summary directory"
        )
    return [
        fold["run_dir"]
        for fold in record["folds"]
        if isinstance(fold, dict)
        and fold.get("state") == "ran"
        and isinstance(fold.get("run_dir"), str)
    ]


def read_skill_series(run_dir):
    """Read the per-row loss gaps a fold's score node left behind.

    ADR-0067's evidence: one entry per ``(series, lead)`` carrying ``d``
    (the loss gaps against the fold's training mean), ``q`` (the
    benchmark MSPE that makes folds comparable), ``stamps`` and
    ``h_steps``. Empty when the fold predates the artifact — which is
    NOT the same as a fold that scored nothing, so the caller must say
    which it found rather than scoring a short walk as a full one.

    Parameters
    ----------
    run_dir : str
        One fold's run directory.

    Returns
    -------
    list of dict
        Every series entry found under ``artifacts/*/skill.json``.

    Examples
    --------
    A fold that scored three names at one lead::

        len(read_skill_series(fold))  # 3
    """
    root = os.path.join(run_dir, ARTIFACTS_DIR)
    if not os.path.isdir(root):
        return []
    found = []
    for node in sorted(os.listdir(root)):
        payload, _why = _load_json(os.path.join(root, node, SKILL_FILE))
        if isinstance(payload, dict) and isinstance(payload.get("series"), list):
            found.extend(s for s in payload["series"] if isinstance(s, dict))
    return found


def read_curve_records(run_dir):
    """Read a fold's per-``(series, lead)`` score rows from the carry.

    The summary side of the same evidence — ``mspe_model``,
    ``mspe_mean``, ``n`` and the Clark–West ``t_stat`` a score node
    carried. Enough for the across-fold half of ADR-0067 and for the
    side columns; never enough for the pooled half.

    Parameters
    ----------
    run_dir : str
        One fold's run directory.

    Returns
    -------
    list of dict
        Every carried record naming a ``symbol`` and a ``lead``.

    Examples
    --------
    Three names at one lead again::

        len(read_curve_records(fold))  # 3
    """
    carry, _why = _load_json(os.path.join(run_dir, CARRY_FILE))
    if not isinstance(carry, dict):
        return []
    found = []
    for outputs in carry.values():
        if not isinstance(outputs, dict):
            continue
        for row in outputs.get("records") or ():
            if isinstance(row, dict) and "symbol" in row and "lead" in row:
                found.append(row)
    return found


def _fold_gaps(run_dir):
    """One fold's per-``(series, lead)`` loss gaps, from the rows it kept.

    ADR-0064's ``predictions.parquet`` is the source: the rows are
    rebuilt into gaps here rather than trusted from a summary, so the
    same file also answers the calibration, cross-sectional and scramble
    questions. A pre-0064 fold's ``skill.json`` still reads, so a walk
    scored under the older artifact keeps its verdict.
    """
    from dskit.pipeline.predictions import read_prediction_series

    return read_prediction_series(run_dir) or read_skill_series(run_dir)


def _by_series(fold_payloads):
    """Group per-fold entries into ``{(series, lead): [entry, ...]}``."""
    grouped = {}
    for entries in fold_payloads:
        for entry in entries:
            key = (str(entry["symbol"]), entry["lead"])
            grouped.setdefault(key, []).append(entry)
    return grouped


def _group_folds(fold_payloads, lead):
    """One panel fold per walk fold: the cross-sectional average gap."""
    from dskit.pipeline.stats import cross_sectional_fold

    folds = []
    for entries in fold_payloads:
        units = [e for e in entries if e["lead"] == lead and len(e.get("d") or ()) >= 2]
        if units:
            fold = cross_sectional_fold(units)
            fold["h_steps"] = units[0].get("h_steps")
            folds.append(fold)
    return folds


def _side_columns(records):
    """Build the Clark–West diagnostics that ride beside a verdict."""
    ts = [float(r["t_stat"]) for r in records if number_ok(r.get("t_stat"))]
    ps = [float(r["p_value"]) for r in records if number_ok(r.get("p_value"))]
    return {
        "cw_t_mean": sum(ts) / len(ts) if ts else None,
        "cw_reject_frac": (sum(1 for p in ps if p <= 0.05) / len(ps) if ps else None),
    }


def _pooled_row(series, lead, folds, records, alpha):
    """One ADR-0067 verdict row from per-row loss gaps."""
    from dskit.pipeline.stats import skill_vs_mean

    h_steps = max(int(folds[0].get("h_steps") or 1), 1)
    out = skill_vs_mean(folds, h_steps=h_steps, alpha=alpha)
    row = {
        "series": series,
        "lead": lead,
        "n_folds": out["n_folds"],
        "n_rows": out["n_rows"],
        "t_pool": out["t_pool"],
        "t_fold": out["t_fold"],
        "r2oos": out["r2oos_pool"],
        "passes": out["passes"],
        "exact": True,
    }
    row.update(_side_columns(records))
    return row


def _summary_row(series, lead, records, alpha):
    """Answer the across-fold half only, as a gapless walk allows."""
    from dskit.pipeline.stats import across_fold_t

    kept = [
        r
        for r in records
        if number_ok(r.get("mspe_mean"))
        and float(r["mspe_mean"]) > 0.0
        and number_ok(r.get("mspe_model"))
        and number_ok(r.get("n"))
    ]
    r2 = [1.0 - float(r["mspe_model"]) / float(r["mspe_mean"]) for r in kept]
    weight = sum(float(r["n"]) * float(r["mspe_mean"]) for r in kept)
    residual = sum(float(r["n"]) * float(r["mspe_model"]) for r in kept)
    fold = across_fold_t(r2) if len(r2) >= 2 else None
    row = {
        "series": series,
        "lead": lead,
        "n_folds": len(r2),
        "n_rows": int(sum(float(r["n"]) for r in kept)),
        "t_pool": None,
        "t_fold": None if fold is None else fold["t"],
        "r2oos": 1.0 - residual / weight if weight else None,
        "passes": None,
        "exact": False,
    }
    row.update(_side_columns(kept))
    return row


def score_walk(summary_dir, alpha=0.05, group="GROUP"):
    """Judge a walk-forward under ADR-0067 — per series and as a group.

    The verdict is the Diebold–Mariano gap against the fold's constant
    training mean, pooled over the walk's folds in time order, AND the
    across-fold t of the per-fold out-of-sample R². Clark–West rides
    beside it as a side column, never as the verdict.

    A walk whose folds saved their per-row predictions (ADR-0064's
    ``predictions.parquet``, or a legacy ``skill.json``) is scored
    EXACTLY — both halves. A walk
    that saved only fold summaries is scored on the across-fold half and
    the R², with ``t_pool`` and ``passes`` left ``None`` and a note
    saying so — the pooled statistic cannot be recovered from an MSPE
    pair, and inventing one would be the defect this rule replaces.

    Parameters
    ----------
    summary_dir : str
        A walk-forward summary directory.
    alpha : float
        One-sided level for both tests, in ``(0, 1)``.
    group : str
        The label the cross-sectional row is reported under. ``None``
        suppresses synthetic aggregation (ADR-0081).

    Returns
    -------
    dict
        ``summary_dir``, ``n_folds``, ``exact`` (whether every fold had
        loss gaps), ``notes`` and ``rows`` — one per ``(series, lead)``
        plus one per lead for the group, each carrying ``t_pool``,
        ``t_fold``, ``r2oos``, ``cw_t_mean``, ``cw_reject_frac`` and
        ``passes``.

    Raises
    ------
    ValueError
        When ``summary_dir`` is not a walk-forward summary.

    Examples
    --------
    A walk of 20 folds over three names at one lead::

        len(score_walk(summary)["rows"])  # 4 — three names and the group
    """
    fold_dirs = walk_fold_dirs(summary_dir)
    gaps = [_fold_gaps(d) for d in fold_dirs]
    records = [read_curve_records(d) for d in fold_dirs]
    exact = bool(fold_dirs) and all(gaps)
    by_record = _by_series(records)
    rows, notes = [], []
    if not exact:
        notes.append(
            f"{sum(1 for g in gaps if not g)}/{len(fold_dirs)} fold(s) saved no "
            "per-row predictions (ADR-0064 artifact absent) — the pooled DM "
            "statistic and the per-timestamp group series are NOT recoverable "
            "from fold summaries; t_pool and passes are reported as unknown."
        )
    by_gap = _by_series(gaps) if exact else {}
    for series, lead in sorted(by_record, key=lambda k: (k[1], k[0])):
        seen = by_record[(series, lead)]
        rows.append(
            _pooled_row(series, lead, by_gap[(series, lead)], seen, alpha)
            if (series, lead) in by_gap
            else _summary_row(series, lead, seen, alpha)
        )
    if group is not None:
        rows.extend(_group_rows(gaps, by_record, exact, alpha, group))
    return {
        "summary_dir": summary_dir,
        "n_folds": len(fold_dirs),
        "exact": exact,
        "notes": notes,
        "rows": rows,
    }


def _group_rows(gaps, by_record, exact, alpha, group):
    """Build the cross-sectional verdict, one row per lead."""
    rows = []
    for lead in sorted({lead for _s, lead in by_record}):
        seen = [r for (s, ell), rs in by_record.items() if ell == lead for r in rs]
        if exact:
            folds = _group_folds(gaps, lead)
            if len(folds) >= 2:
                rows.append(_pooled_row(group, lead, folds, seen, alpha))
                continue
        rows.append(_summary_row(group, lead, seen, alpha))
    return rows


def format_skill(scored):
    """Render :func:`score_walk`'s rows as one markdown table.

    Parameters
    ----------
    scored : mapping
        A :func:`score_walk` result.

    Returns
    -------
    str
        The table, then every note, then a blank-safe trailing newline.

    Examples
    --------
    Printed straight to a terminal::

        print(format_skill(score_walk(summary)))
    """
    columns = [
        "series",
        "lead",
        "n_folds",
        "n_rows",
        "t_pool",
        "t_fold",
        "r2oos",
        "cw_t_mean",
        "cw_reject_frac",
        "passes",
    ]
    body = "\n".join(
        _render_table(
            columns, [[row.get(c) for c in columns] for row in scored["rows"]]
        )
    )
    tail = "".join("\n\nnote: " + n for n in scored["notes"])
    return body + tail


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
        path for path in params if not any(_walk(run.config, path)[0] for run in runs)
    )


def _all_metrics(runs):
    """Every metric key any run reported, sorted."""
    return tuple(sorted({key for run in runs for key in run.metrics}))


def _lead_arrays(units, lead):
    """Flatten one lead's units into aligned stamp/series/y/yhat lists."""
    stamps, series, y, yhat = [], [], [], []
    for unit in units:
        if unit["lead"] != lead:
            continue
        stamps.extend(unit["stamps"])
        series.extend([unit["symbol"]] * len(unit["stamps"]))
        y.extend(unit["y"])
        yhat.extend(unit["yhat"])
    return stamps, series, y, yhat


def _fold_ordering(units, lead):
    """Extract one fold's ordering evidence at one lead."""
    from dskit.pipeline.ordering import (
        cross_section_by_stamp,
        demean_by_series,
        pooled_name_time_ic,
    )

    stamps, series, y, yhat = _lead_arrays(units, lead)
    raw = cross_section_by_stamp(stamps, series, y, yhat)
    demeaned = cross_section_by_stamp(
        stamps,
        series,
        demean_by_series(series, y),
        demean_by_series(series, yhat),
    )
    pooled = pooled_name_time_ic(y, yhat)["ic"] if len(y) >= 2 else None
    return raw, demeaned, pooled


def _new_lead_accumulator(h_steps):
    """Build the per-lead ordering accumulator a walk fills fold by fold."""
    return {
        "rho": [],
        "rho_demeaned": [],
        "n_names_all": [],
        "n_skipped": 0,
        "pooled": [],
        "folds_positive": 0,
        "n_folds": 0,
        "h_steps": h_steps,
    }


def _accumulate_ordering(acc, units, lead):
    """Fold one fold's ordering evidence into a lead's accumulator."""
    raw, demeaned, pooled = _fold_ordering(units, lead)
    acc["rho"].extend(raw["rho"])
    acc["rho_demeaned"].extend(demeaned["rho"])
    acc["n_names_all"].extend(raw["n_names_all"])
    acc["n_skipped"] += raw["n_skipped"]
    if pooled is not None:
        acc["pooled"].append(pooled)
    if raw["rho"] and sum(raw["rho"]) / len(raw["rho"]) > 0.0:
        acc["folds_positive"] += 1
    acc["n_folds"] += 1


def _slope_reading(summary):
    """Say in words what a mean calibration slope means for sizing."""
    mean, t0, t1 = summary["slope_mean"], summary["t_vs_0"], summary["t_vs_1"]
    if t0 is None or abs(t0) < 2.0:
        return "no size: slope indistinguishable from zero — rank only, if anything"
    if t1 is not None and abs(t1) < 2.0:
        return "size usable: slope indistinguishable from one"
    if 0.0 < mean < 1.0:
        return f"over-reacts: real information at {mean:.2f}x scale, shrink it"
    if mean >= 1.0:
        return "under-reacts: the forecast moves less than the outcome does"
    return "wrong sign: the forecast is anti-correlated with the outcome"


def _calibration_rows(slopes, alpha):
    """Turn per-fold slopes into one summary row per (series, lead)."""
    from dskit.pipeline.ordering import calibration_across_folds

    rows = []
    for series, lead in sorted(slopes, key=lambda k: (k[1], k[0])):
        values = slopes[(series, lead)]
        if len(values) < 2:
            continue
        summary = calibration_across_folds(values)
        rows.append(
            {
                "series": series,
                "lead": lead,
                "n_folds": summary["n_folds"],
                "slope": summary["slope_mean"],
                "slope_se": summary["slope_se"],
                "t_vs_0": summary["t_vs_0"],
                "t_vs_1": summary["t_vs_1"],
                "frac_pos": summary["frac_positive"],
                "reading": _slope_reading(summary),
                "alpha": alpha,
            }
        )
    return rows


def _ordering_rows(lead_acc):
    """Turn per-lead accumulators into the cross-sectional verdict rows."""
    from dskit.pipeline.ordering import ic_from_rho, ordering_verdict
    from dskit.pipeline.stats import across_fold_t

    rows = []
    for lead in sorted(lead_acc):
        acc = lead_acc[lead]
        pooled = ic_from_rho(
            acc["rho"],
            acc["n_names_all"],
            h_steps=acc["h_steps"],
            n_skipped=acc["n_skipped"],
        )
        demeaned = ic_from_rho(
            acc["rho_demeaned"],
            acc["n_names_all"],
            h_steps=acc["h_steps"],
        )
        verdict = ordering_verdict(
            pooled,
            demeaned,
            fold_positive=acc["folds_positive"],
            n_folds=acc["n_folds"],
        )
        name_time = across_fold_t(acc["pooled"]) if len(acc["pooled"]) >= 2 else None
        rows.append(
            {
                "lead": lead,
                "n_stamps": pooled["n_stamps"],
                "names_min": pooled["n_names"]["min"],
                "names_median": pooled["n_names"]["median"],
                "names_max": pooled["n_names"]["max"],
                "xs_ic": pooled["ic"],
                "xs_ic_t": pooled["ic_t"],
                "xs_ic_p": pooled["ic_p"],
                "frac_pos": pooled["frac_pos"],
                "xs_ic_demeaned": demeaned["ic"],
                "retained": verdict["retained"],
                "pooled_name_time_ic": None if name_time is None else name_time["mean"],
                "folds_positive": acc["folds_positive"],
                "n_folds": acc["n_folds"],
                "usable": pooled["usable"],
                "passes": verdict["passes"],
                "reasons": verdict["reasons"],
                "unusable_reason": pooled["unusable_reason"],
            }
        )
    return rows


def score_ordering(summary_dir, alpha=0.05):
    """Measure a walk's ORDERING and its SIZE apart (ADR-0068).

    Two answers that are allowed to differ, read off the rows a walk
    saved. The calibration slope regresses the outcome on the forecast
    per fold and pools the slopes across folds — a slope near one means
    the predicted magnitude is usable, near zero means at best the rank
    is. The cross-sectional score ranks the names WITHIN each instant,
    correlates the two orderings, and tests the resulting time series
    with an HAC band; its demeaned twin says whether that ordering is
    timing or a standing per-name tilt.

    The pooled ``(name, time)`` correlation the scan already reports
    rides along under its own name, never under the cross-sectional
    one: they answer different questions and confusing them is the
    defect this function exists to end.

    Folds are read and REDUCED one at a time — a walk's rows are never
    all in memory at once.

    Parameters
    ----------
    summary_dir : str
        A walk-forward summary directory.
    alpha : float
        One-sided level, in ``(0, 1)``.

    Returns
    -------
    dict
        ``summary_dir``, ``n_folds``, ``exact`` (whether every fold
        saved rows), ``notes``, ``calibration`` (one row per series and
        lead) and ``ordering`` (one row per lead, carrying ``usable``
        and the reason when it is false).

    Raises
    ------
    ValueError
        When ``summary_dir`` is not a walk-forward summary.

    Examples
    --------
    Both halves of one walk's answer::

        print(format_ordering(score_ordering(summary)))
    """
    from dskit.pipeline.ordering import calibration_slope

    fold_dirs = walk_fold_dirs(summary_dir)
    slopes, lead_acc, seen = {}, {}, 0
    for run_dir in fold_dirs:
        units = _fold_gaps(run_dir)
        if not units:
            continue
        seen += 1
        for unit in units:
            if len(unit.get("y") or ()) < 3:
                continue
            steps = max(int(unit.get("h_steps") or 1), 1)
            try:
                fit = calibration_slope(unit["y"], unit["yhat"], h_steps=steps)
            except ValueError:
                continue
            slopes.setdefault((unit["symbol"], unit["lead"]), []).append(fit["slope"])
        for lead in sorted({u["lead"] for u in units}):
            steps = max(int(units[0].get("h_steps") or 1), 1)
            acc = lead_acc.setdefault(lead, _new_lead_accumulator(steps))
            _accumulate_ordering(acc, units, lead)
        units = None
    notes = []
    if seen < len(fold_dirs):
        notes.append(
            f"{len(fold_dirs) - seen}/{len(fold_dirs)} fold(s) saved no per-row "
            "predictions (ADR-0064 artifact absent) — neither measure is "
            "recoverable from a fold summary, so those folds are simply absent"
        )
    for row in _ordering_rows(lead_acc):
        if not row["usable"]:
            notes.append(f"lead {row['lead']}: {row['unusable_reason']}")
    return {
        "summary_dir": summary_dir,
        "n_folds": seen,
        "exact": bool(fold_dirs) and seen == len(fold_dirs),
        "notes": notes,
        "calibration": _calibration_rows(slopes, alpha),
        "ordering": _ordering_rows(lead_acc),
    }


def format_ordering(scored):
    """Render :func:`score_ordering` as two clearly separated tables.

    Parameters
    ----------
    scored : mapping
        A :func:`score_ordering` result.

    Returns
    -------
    str
        The size table, then the ordering table, then every note.

    Examples
    --------
    Printed straight to a terminal::

        print(format_ordering(score_ordering(summary)))
    """
    size_cols = [
        "series",
        "lead",
        "n_folds",
        "slope",
        "slope_se",
        "t_vs_0",
        "t_vs_1",
        "frac_pos",
        "reading",
    ]
    order_cols = [
        "lead",
        "n_stamps",
        "names_min",
        "names_median",
        "names_max",
        "xs_ic",
        "xs_ic_t",
        "xs_ic_p",
        "frac_pos",
        "xs_ic_demeaned",
        "retained",
        "pooled_name_time_ic",
        "folds_positive",
        "usable",
        "passes",
    ]
    size = "\n".join(
        _render_table(
            size_cols,
            [[row.get(c) for c in size_cols] for row in scored["calibration"]],
        )
    )
    order = "\n".join(
        _render_table(
            order_cols,
            [[row.get(c) for c in order_cols] for row in scored["ordering"]],
        )
    )
    tail = "".join("\n\nnote: " + n for n in scored["notes"])
    return (
        "SIZE — calibration slope of outcome on forecast, pooled over folds\n\n"
        + size
        + "\n\nORDER — rank correlation WITHIN each instant (xs_ic), against the "
        "pooled\n(name, time) correlation the scan reports "
        "(pooled_name_time_ic). They are\nnot the same number: the pooled one "
        "mixes 'when' with 'which name'.\n\n" + order + tail
    )


def _default_cell_key(summary_dir):
    """Name the knobs a walk states about itself when none are supplied."""
    return {"walk": os.path.basename(os.path.normpath(summary_dir))}


def _accumulate_cell(store, key, unit, session_of):
    """Fold one (series, lead) unit's rows into that cell's session totals."""
    from dskit.pipeline.attempts import merge_session_totals, session_totals

    cell = store.setdefault(
        key,
        {"totals": {}, "folds": [], "h_steps": max(int(unit.get("h_steps") or 1), 1)},
    )
    q = float(unit["q"])
    if q <= 0.0:
        return
    scaled = [v / q for v in unit["d"]]
    merge_session_totals(
        cell["totals"], session_totals(unit["stamps"], scaled, session_of)
    )
    cell["folds"].append({"d": list(unit["d"]), "q": q})


def _cell_skill(cell, alpha):
    """Reduce one cell's ADR-0067 verdict to the scalars P8 reads."""
    from dskit.pipeline.stats import skill_vs_mean

    if len(cell["folds"]) < 2:
        return None
    out = skill_vs_mean(cell["folds"], h_steps=cell["h_steps"], alpha=alpha)
    return {
        k: out[k]
        for k in ("passes", "t_pool", "p_pool", "t_fold", "r2oos_pool", "n_rows")
    }


def walk_cells(summary_dir, key=None, session_of=None, alpha=0.05, group="GROUP"):
    """Reduce one walk to the per-cell evidence the many-attempts bar takes.

    A cell is one ``(outcome unit, horizon)`` pair under whatever other
    knobs ``key`` names — model, row spacing, price field, feature set.
    Each is reduced to per-SESSION sums of the scale-free loss gaps plus
    its ADR-0067 verdict, and the rows are dropped: a bar over dozens of
    walks then holds a few hundred numbers per cell instead of a few
    hundred thousand. One walk's rows are in memory at a time, never
    two.

    Parameters
    ----------
    summary_dir : str
        A walk-forward summary directory.
    key : mapping or None
        The knobs shared by every cell in this walk. ``None`` uses the
        walk's directory name, which identifies the run but NOT what it
        varied — pass the real knobs when the count must be honest.
    session_of : callable or None
        Maps a timestamp to its session key. ``None`` takes
        :func:`~dskit.pipeline.attempts.utc_day`.
    alpha : float
        One-sided level for the per-cell skill test, in ``(0, 1)``.
    group : str
        Label for the cross-sectional row, which is its own outcome unit.
        ``None`` suppresses that synthetic cell (ADR-0081).

    Returns
    -------
    list of dict
        One per cell: ``cell`` (the id), ``key``, ``totals``
        (``{session: [sum, count]}``), ``skill`` (or ``None`` when the
        walk gave it fewer than two folds) and ``n_folds``.

    Raises
    ------
    ValueError
        When ``summary_dir`` is not a walk-forward summary.

    Examples
    --------
    Three names and the group at one lead::

        len(walk_cells(summary))  # 4
    """
    from dskit.pipeline.attempts import cell_id

    base = dict(key) if key else _default_cell_key(summary_dir)
    store = {}
    for run_dir in walk_fold_dirs(summary_dir):
        units = [u for u in _fold_gaps(run_dir) if len(u.get("d") or ()) >= 2]
        for unit in units:
            _accumulate_cell(store, (unit["symbol"], unit["lead"]), unit, session_of)
        if group is not None:
            for lead in sorted({u["lead"] for u in units}):
                panel = _group_folds([units], lead)
                if panel:
                    _accumulate_cell(store, (group, lead), panel[0], session_of)
        units = None
    out = []
    for (series, lead), cell in sorted(store.items(), key=lambda kv: kv[0][::-1]):
        knobs = {**base, "series": series, "horizon": lead}
        out.append(
            {
                "cell": cell_id(knobs),
                "key": knobs,
                "totals": cell["totals"],
                "skill": _cell_skill(cell, alpha),
                "n_folds": len(cell["folds"]),
            }
        )
        cell["folds"] = []
    return out


def _collect_cells(summary_dirs, keys, registry, session_of, alpha, group):
    """Reduce every walk in turn, recording each cell as an attempt."""
    collected = {}
    for i, summary_dir in enumerate(summary_dirs):
        key = keys[i] if keys and i < len(keys) else None
        for cell in walk_cells(
            summary_dir, key=key, session_of=session_of, alpha=alpha, group=group
        ):
            collected[cell["cell"]] = cell
            if registry is not None:
                skill = cell["skill"] or {}
                registry.record(
                    cell["key"],
                    walk=summary_dir,
                    t_pool=skill.get("t_pool"),
                    r2oos=skill.get("r2oos_pool"),
                    n_folds=cell["n_folds"],
                )
    return collected


def _series_family(cell):
    """Default multiplicity family: one family per scored outcome unit."""
    return cell["key"]["series"]


def _family_count(registry, family_of, family):
    """Count registered cells assigned to one injected family."""
    if registry is None:
        return None
    return sum(
        1
        for row in registry.cells().values()
        if family_of({"key": row["key"]}) == family
    )


def score_bar(
    summary_dirs,
    keys=None,
    registry=None,
    session_of=None,
    n_boot=10000,
    seed=0,
    alpha=0.05,
    group="GROUP",
    family_of=None,
):
    """Raise ADR-0067's mark for the many attempts behind it (ADR-0069).

    One family per OUTCOME UNIT — each name, and the group — because a
    family is the set of tries that competed for the same answer. Within
    a family every cell is resampled JOINTLY under one coin per trading
    session, so near-identical horizons cost barely more than a single
    attempt, and the 95th percentile of the best-of-all-cells statistic
    becomes the pass mark, floored at a t of 3.0. P5 still decides; this
    only raises the mark, and a cell that fails P5 fails here whatever
    its statistic.

    Parameters
    ----------
    summary_dirs : list or tuple of str
        Walk-forward summary directories, reduced one at a time.
    keys : list of mapping or None
        The knobs of each walk, aligned with ``summary_dirs``.
    registry : AttemptRegistry or None
        The ledger of every cell ever run. When given, each cell is
        recorded and its family count comes from the LEDGER, not from
        tonight's directories — an attempt made last week cost the same
        alpha as one made an hour ago.
    session_of : callable or None
        Session key for a timestamp.
    n_boot : int
        Bootstrap replicates, ``>= 100``.
    seed : int
        Seeds the session coins.
    alpha : float
        Family-wise level, in ``(0, 1)``.

    group : str or None
        Synthetic aggregate label. ``None`` suppresses aggregate cells.
    family_of : callable or None
        Strategy mapping one collected cell to its family key. The default
        keeps one family per series; a constant strategy makes one study-wide
        family without changing :func:`max_bar` (ADR-0081).

    Returns
    -------
    dict
        ``families`` — one entry per outcome unit with ``bar`` (a
        :func:`~dskit.pipeline.attempts.max_bar` result) and
        ``verdicts`` (a
        :func:`~dskit.pipeline.attempts.bar_verdict` per cell) — plus
        ``k_registry``, ``n_cells`` and ``notes``.

    Raises
    ------
    ValueError
        On an empty ``summary_dirs``, or when no walk kept the rows.

    Examples
    --------
    Tonight's walks judged against every attempt ever made::

        out = score_bar(walks, registry=AttemptRegistry(ledger))
        out["families"]["JPM"]["bar"]["pass_mark"]  # 3.0
    """
    from dskit.pipeline.attempts import bar_verdict, max_bar

    if not summary_dirs:
        raise ValueError("summary_dirs must name at least one walk")
    collected = _collect_cells(
        summary_dirs,
        keys,
        registry,
        session_of,
        alpha,
        group,
    )
    if not collected:
        raise ValueError(
            "no walk kept per-row predictions — the bar resamples stored "
            "scores, and there are none (ADR-0064 artifact absent)"
        )
    family_of = _series_family if family_of is None else family_of
    assigned = {ident: family_of(cell) for ident, cell in collected.items()}
    bad = sorted(
        {
            repr(value)
            for value in assigned.values()
            if not isinstance(value, str) or not value
        }
    )
    if bad:
        raise ValueError(f"family_of must return non-empty strings, got {bad}")
    families, notes = {}, []
    for unit in sorted(set(assigned.values())):
        cells = {
            c["cell"]: {s: tuple(v) for s, v in c["totals"].items()}
            for c in collected.values()
            if assigned[c["cell"]] == unit
        }
        declared = _family_count(registry, family_of, unit)
        try:
            bar = max_bar(
                cells,
                n_boot=n_boot,
                seed=seed,
                alpha=alpha,
                k_declared=declared,
            )
        except ValueError as exc:
            notes.append(f"{unit}: no bar — {exc}")
            continue
        notes.extend(f"{unit}: {n}" for n in bar["notes"])
        families[unit] = {
            "bar": bar,
            "verdicts": [
                bar_verdict(
                    row["cell"],
                    bar,
                    skill=collected[row["cell"]]["skill"],
                    alpha=alpha,
                )
                for row in bar["rows"]
            ],
            "keys": {
                c["cell"]: c["key"]
                for c in collected.values()
                if assigned[c["cell"]] == unit
            },
        }
    return {
        "families": families,
        "k_registry": registry.count() if registry is not None else None,
        "n_cells": len(collected),
        "notes": notes,
    }


def format_bar(scored):
    """Render :func:`score_bar` as one table per outcome unit.

    Parameters
    ----------
    scored : mapping
        A :func:`score_bar` result.

    Returns
    -------
    str
        A header per family stating the mark and what it was worth,
        then its cells, then every note.

    Examples
    --------
    Printed straight to a terminal::

        print(format_bar(score_bar(walks)))
    """
    columns = [
        "horizon",
        "t",
        "pass_mark",
        "adj_p",
        "r2oos",
        "r2oos_lower",
        "p5_passes",
        "passes",
        "why",
    ]
    blocks = []
    for unit in sorted(scored["families"]):
        family = scored["families"][unit]
        bar = family["bar"]
        rows = []
        for verdict in family["verdicts"]:
            key = family["keys"][verdict["cell"]]
            rows.append(
                [
                    key.get("horizon"),
                    verdict["t"],
                    verdict["pass_mark"],
                    verdict["adj_p"],
                    verdict["r2oos"],
                    verdict["r2oos_lower"],
                    _p5_flag(verdict),
                    verdict["passes"],
                    verdict["reasons"][0].split(" — ")[0] if verdict["reasons"] else "",
                ]
            )
        blocks.append(
            f"{unit} — {bar['k']} cell(s) resampled of {bar['k_declared']} "
            f"attempted; c* {bar['c_star']:.3f}, pass mark "
            f"{bar['pass_mark']:.3f}, worth about {bar['k_implied']:.0f} "
            f"independent tries (Bonferroni {bar['bonferroni']:.2f})\n\n"
            + "\n".join(_render_table(columns, rows))
        )
    tail = "".join("\n\nnote: " + n for n in scored["notes"])
    return "\n\n".join(blocks) + tail


def _p5_flag(verdict):
    """Say whether P5's own decision was the thing that failed."""
    return not any(r.startswith("P5's skill test") for r in verdict["reasons"])
