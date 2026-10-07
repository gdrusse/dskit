"""Roll a lane-keyed workflow batch up into one cross-lane table (ADR-0227 family).

``workflow-batch`` leaves one work dir per lane plus ``batch.json``. This module
reads each lane's study outputs and writes ONE row per lane to ``rollup.csv`` and
a self-contained, sortable ``report.html``: lane, batch status, the declared
value columns (counts of dates, folds, features, the winner, ...), the chosen
model's paired block-bootstrap skill interval versus the reference for each
declared block, a verdict, and the failures.

The verdict is ``accepted`` when, for EVERY block in ``blocks``, the interval's
lower bound is above ``min_lower_bound``; ``rejected`` when every block is
present and one is not above it; ``unscored`` when a declared block has no finite
interval, a declared column or source is missing, unreadable or fails its ledger
hash, or the interval rows are ambiguous; ``failed`` when the lane's batch status
is not ok; ``pending`` when it never ran. The chosen model is NOT guessed from the
interval rows: ``intervals.model`` names a per-lane source (the lane's own winner
file) and field, and rows are matched on (model, reference, block) with ``where``
(for example the metric) required, so the verdict never depends on row order.
A source may name ``step`` and ``output`` (and ``bound`` when the ledger output is
a directory): its content hash is then checked against the lane's ledger like
:mod:`dskit.pipeline.workflow_report` does.

Nothing project-specific lives here. File paths are patterns over ``{work_dir}``
and ``{lane}``; every field name, filter, block and threshold is in the spec.
A value column is a strategy object in :data:`COLUMN_KINDS` (``cell``: one field
of the first matching row; ``count``: the number of matching rows); a project
adds a kind by subclassing :class:`ColumnKind` and calling
:func:`register_column_kind`. A missing source of an ok lane leaves the cell
empty and adds a note to ``failures``. Stdlib only.

Run it with ``python -m dskit.pipeline.workflow_rollup <spec.json>``; exit 0
wrote the files, 1 refused.
"""

from __future__ import annotations

import csv
import html
import io
import json
import os
from abc import ABC, abstractmethod

from dskit.pipeline.records import number_ok
from dskit.pipeline.workflow import EXIT_ERROR, EXIT_OK, LANE_SEP, LEDGER_NAME, path_hash
from dskit.pipeline.workflow_batch import STATUS_OK, STATUS_PENDING, land_bytes
from dskit.pipeline.workflow_report import READERS, row_matches

__all__ = [
    "RollupError", "ColumnKind", "CellColumn", "CountColumn", "COLUMN_KINDS",
    "register_column_kind", "WorkflowRollup", "main", "CSV_NAME", "HTML_NAME",
    "ACCEPTED", "REJECTED", "UNSCORED", "FAILED", "PENDING",
]

ENCODING = "utf-8"
CSV_NAME = "rollup.csv"
HTML_NAME = "report.html"
LANE_COLUMN = "lane"
STATUS_COLUMN = "status"
VERDICT_COLUMN = "verdict"
FAILURES_COLUMN = "failures"
ACCEPTED, REJECTED, UNSCORED, FAILED, PENDING = (
    "accepted", "rejected", "unscored", "failed", "pending",
)
FORMULA_LEADS = ("=", "+", "-", "@")
MISSING_TEXT = "n/a"
TOP_KEYS = {
    "batch", "output_dir", "title", "min_lower_bound", "blocks", "intervals",
    "columns", "notes",
}
SOURCE_KEYS = {"path", "format", "rows", "step", "output", "bound"}
COLUMN_KEYS = {"name", "kind", "source", "field", "where", "notes"}
INTERVAL_KEYS = {"source", "where", "reference", "model", "fields", "columns", "notes"}
INTERVAL_REQUIRED = {"source", "where", "reference", "model", "fields", "columns"}
FIELD_NAMES = ("model", "reference", "block", "lo", "hi", "point")
COLUMN_NAMES = ("skill", "lo", "hi", "model")
RESERVED = (LANE_COLUMN, STATUS_COLUMN, VERDICT_COLUMN, FAILURES_COLUMN)
PAGE_STYLE = (
    "body{font:14px system-ui,sans-serif;margin:0 auto;max-width:96rem;"
    "padding:1rem 1.25rem;background:#fff;color:#111}"
    "table{border-collapse:collapse;display:block;overflow-x:auto}"
    "th,td{border:1px solid #ccc;padding:.25rem .5rem;text-align:left}"
    "th{background:#f0f0f0;cursor:pointer;white-space:nowrap}"
    "td.accepted{background:#d8f0d8}td.rejected{background:#f6dede}"
    "td.failed{background:#f3d7a8}"
    "@media(prefers-color-scheme:dark){body{background:#111;color:#eee}"
    "th{background:#222}th,td{border-color:#444}td.accepted{background:#1d3a1d}"
    "td.rejected{background:#3b1d1d}td.failed{background:#4a3a14}}"
)
SORT_SCRIPT = (
    "document.querySelectorAll('th').forEach(function(h,i){h.onclick=function(){"
    "var b=h.closest('table').tBodies[0],r=Array.prototype.slice.call(b.rows),"
    "d=h.dataset.dir=h.dataset.dir==='1'?'-1':'1';r.sort(function(x,y){"
    "var a=x.cells[i].textContent,c=y.cells[i].textContent,"
    "n=parseFloat(a),m=parseFloat(c);"
    "if(!isNaN(n)&&!isNaN(m))return(n-m)*d;return a.localeCompare(c)*d;});"
    "r.forEach(function(x){b.appendChild(x)});};});"
)


class RollupError(ValueError):
    """A refused rollup: ``problems`` lists every reason."""

    def __init__(self, problems):
        self.problems = [problems] if isinstance(problems, str) else list(problems)
        super().__init__("; ".join(self.problems))


def _expand(template, **values):
    """Fill a ``{name}`` template; the ONE templating method of this module."""
    try:
        return str(template).format(**values)
    except (KeyError, IndexError, ValueError) as err:
        raise RollupError(f"template {template!r} cannot be filled with {sorted(values)}: {err!r}")


def _matches(row, where, lane):
    """Return True when every ``where`` field reads as its (lane-expanded) value."""
    return row_matches(row, {k: _expand(v, lane=lane) for k, v in (where or {}).items()})


def _number(value):
    """Return a finite float, or None (text, bool, NaN and infinity are not numbers)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number_ok(number) and not isinstance(value, bool) else None


class _Source:
    """A lane-pattern file the rollup reads rows from, optionally ledger-verified."""

    def __init__(self, spec):
        self.spec = spec

    def path(self, work_dir, lane):
        """Return the source path for one lane."""
        return _expand(self.spec["path"], work_dir=work_dir, lane=lane)

    def _read(self, path):
        rows = READERS[self.spec["format"]].read(path, {"rows": self.spec.get("rows", "")})
        if not all(isinstance(r, dict) for r in rows):
            raise ValueError("a row is not an object")
        return rows

    @staticmethod
    def _header_problem(path):
        with open(path, encoding=ENCODING, newline="") as handle:
            header = next(csv.reader(handle), [])
        return "duplicate column headers" if len(set(header)) != len(header) else None

    def _ledger_problem(self, path, work_dir, lane):
        """Return a problem when the file's hash differs from the lane's ledger."""
        step, out = self.spec.get("step"), self.spec.get("output")
        if step is None:
            return None
        try:
            with open(os.path.join(work_dir, LEDGER_NAME), encoding=ENCODING) as handle:
                entry = json.load(handle)["steps"].get(f"{step}{LANE_SEP}{lane}") or {}
        except (OSError, ValueError, KeyError):
            return "ledger missing or unreadable"
        recorded = (entry.get("outputs") or {}).get(out)
        bound = _expand(self.spec["bound"], work_dir=work_dir, lane=lane) if "bound" in self.spec else path
        if recorded is None:
            return f"ledger has no hash for {step}.{out}"
        return None if path_hash(bound) == recorded else f"hash differs from ledger for {step}.{out}"

    def rows(self, work_dir, lane):
        """Return ``(rows, problem)``: rows are None when the file cannot be used."""
        path = self.path(work_dir, lane)
        if not os.path.isfile(path):
            return None, "source missing"
        try:
            problem = self._header_problem(path) if self.spec["format"] == "csv" else None
            problem = problem or self._ledger_problem(path, work_dir, lane)
            return (None, problem) if problem else (self._read(path), None)
        except (ValueError, OSError, csv.Error) as err:
            return None, f"unreadable: {err}"


class ColumnKind(ABC):
    """Computes one value column of a lane's row from the rows of its source.

    Examples
    --------
    Add a kind that returns the largest value of a field::

        class MaxColumn(ColumnKind):
            def value(self, rows, column, lane):
                return max(float(r[column["field"]]) for r in rows)

        register_column_kind("max", MaxColumn())
    """

    @abstractmethod
    def value(self, rows, column, lane):
        """Return the cell (None when the value is absent).

        Parameters
        ----------
        rows : list of dict
            The source's rows.
        column : dict
            The column spec (``field``, ``where``).
        lane : str
            The lane value.

        Returns
        -------
        object
            The cell value, or None.
        """


class CellColumn(ColumnKind):
    """One field of the first row matching ``where`` (all rows when absent).

    Examples
    --------
    Read the ``winner`` of a key/value document::

        CellColumn().value(rows, {"field": "value", "where": {"key": "winner"}}, "A")
        # -> 'mlp'
    """

    def value(self, rows, column, lane):
        """Return the field of the first matching row, or None."""
        for row in rows:
            if _matches(row, column.get("where"), lane):
                return row.get(column["field"])
        return None


class CountColumn(ColumnKind):
    """The number of rows matching ``where``.

    Examples
    --------
    Count the rows of a feature list::

        CountColumn().value(rows, {}, "A")
        # -> 58
    """

    def value(self, rows, column, lane):
        """Return the matching row count."""
        return sum(1 for row in rows if _matches(row, column.get("where"), lane))


COLUMN_KINDS = {"cell": CellColumn(), "count": CountColumn()}


def register_column_kind(name, kind):
    """Register a :class:`ColumnKind` under a ``kind`` name.

    Parameters
    ----------
    name : str
        The ``kind`` value a column uses.
    kind : ColumnKind
        The strategy object.

    Raises
    ------
    ValueError
        If ``kind`` is not a new :class:`ColumnKind` instance.
    """
    if not isinstance(kind, ColumnKind) or name in COLUMN_KINDS:
        raise ValueError(f"column kind {name!r} must be a new ColumnKind instance")
    COLUMN_KINDS[name] = kind


class WorkflowRollup:
    """Build the cross-lane table, ``rollup.csv`` and ``report.html``.

    Parameters
    ----------
    spec : dict
        ``batch`` (path to ``batch.json``), ``output_dir``, ``title``,
        ``min_lower_bound`` (number), ``blocks`` (list of block sizes),
        ``intervals`` (``source``; ``where``, a required non-empty filter such as
        the metric; ``reference``; ``model``, a cell column spec naming the lane's
        chosen model from its own winner source; ``fields`` naming the
        ``model/reference/block/lo/hi/point`` fields; ``columns`` naming the output
        ``skill/lo/hi`` columns with a ``{block}`` placeholder and the ``model``
        column), ``columns`` (list of ``name``, ``kind``, ``source``, ``field``,
        optional ``where``) and ``notes``. A source is ``path`` (``{work_dir}``,
        ``{lane}``), ``format``, optional ``rows`` (dotted path to the row list in
        a JSON document) and optional ``step`` + ``output`` (+ ``bound``) to verify
        against the lane's ledger.

    Raises
    ------
    RollupError
        On any invalid spec or unusable ``batch.json``.

    Examples
    --------
    Roll up a finished batch::

        rollup = WorkflowRollup({
            "batch": "runs/batch.json", "output_dir": "runs/rollup",
            "title": "Study", "min_lower_bound": 0.0, "blocks": [30, 60],
            "intervals": {
                "source": {"path": "{work_dir}/cmp.json", "format": "json",
                           "rows": "intervals"},
                "where": {"metric": "crps"}, "reference": "base",
                "model": {"source": {"path": "{work_dir}/best.json", "format": "json"},
                          "kind": "cell", "field": "value", "where": {"key": "name"}},
                "fields": {"model": "model", "reference": "reference",
                           "block": "block", "lo": "lo", "hi": "hi", "point": "point"},
                "columns": {"skill": "skill_{block}", "lo": "lo_{block}",
                            "hi": "hi_{block}", "model": "model"}},
            "columns": [],
        })
        rollup.write()
    """

    def __init__(self, spec):
        problems = self._spec_problems(spec)
        if problems:
            raise RollupError(problems)
        self.spec = spec
        self.blocks = [float(b) for b in spec["blocks"]]
        self.threshold = float(spec["min_lower_bound"])
        self.interval = spec["intervals"]
        cols = self.interval["columns"]
        self.names = {
            b: {k: _expand(cols[k], block=_block_text(b)) for k in ("skill", "lo", "hi")}
            for b in self.blocks
        }
        self._check_names()
        self._check_templates()

    def _check_names(self):
        names = self._column_names()
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise RollupError(f"duplicate or reserved output column names {dupes}")

    def _check_templates(self):
        """Fill every path and where template once so a bad one fails at construction."""
        sources = [c["source"] for c in self.spec["columns"]] + [self.interval["source"], self.interval["model"]["source"]]
        for source in sources:
            _expand(source["path"], work_dir="", lane="x")
            _expand(source.get("bound", ""), work_dir="", lane="x")
        wheres = [c.get("where") for c in self.spec["columns"]] + [self.interval["where"], self.interval["model"].get("where")]
        for where in wheres:
            for value in (where or {}).values():
                _expand(value, lane="x")

    @staticmethod
    def _source_problems(label, source):
        if (not isinstance(source, dict) or set(source) - SOURCE_KEYS
                or not {"path", "format"} <= set(source)
                or not all(isinstance(source[k], str) for k in source)):
            return [f"{label}: source needs text path and format (optional rows, step, output, bound)"]
        problems = []
        if source["format"] not in READERS:
            problems.append(f"{label}: format {source['format']!r} has no reader")
        if ("step" in source) != ("output" in source):
            problems.append(f"{label}: step and output come together")
        if "bound" in source and "step" not in source:
            problems.append(f"{label}: bound needs step and output")
        return problems

    @classmethod
    def _spec_problems(cls, spec):
        if not isinstance(spec, dict):
            return ["spec must be an object"]
        problems = [f"unknown spec key {k!r}" for k in sorted(set(spec) - TOP_KEYS)]
        problems += [f"spec key {k!r} is required" for k in sorted(TOP_KEYS - {"notes"} - set(spec))]
        for key in ("batch", "output_dir", "title"):
            if key in spec and not (isinstance(spec[key], str) and spec[key]):
                problems.append(f"{key} must be non-empty text")
        blocks = spec.get("blocks")
        if "blocks" in spec and (
            not isinstance(blocks, list) or not blocks
            or any(_number(b) is None for b in blocks)
            or len({float(b) for b in blocks if _number(b) is not None}) != len(blocks)
        ):
            problems.append("blocks must be a non-empty list of distinct numbers")
        if "min_lower_bound" in spec and _number(spec["min_lower_bound"]) is None:
            problems.append("min_lower_bound must be a finite number")
        problems += cls._interval_problems(spec.get("intervals"))
        columns = spec.get("columns", [])
        if not isinstance(columns, list):
            return problems + ["columns must be a list"]
        for column in columns:
            problems += cls._column_problems(column)
        return problems

    @classmethod
    def _interval_problems(cls, block):
        if block is None:
            return []
        if not isinstance(block, dict) or set(block) - INTERVAL_KEYS:
            return [f"intervals: keys must be among {sorted(INTERVAL_KEYS)}"]
        problems = [f"intervals.{k} is required" for k in sorted(INTERVAL_REQUIRED - set(block))]
        problems += cls._source_problems("intervals", block.get("source"))
        if "reference" in block and not (isinstance(block["reference"], str) and block["reference"]):
            problems.append("intervals.reference must be non-empty text")
        if "where" in block and not (isinstance(block["where"], dict) and block["where"]):
            problems.append("intervals.where must be a non-empty object (for example the metric)")
        if "fields" in block and (not isinstance(block["fields"], dict) or set(block["fields"]) != set(FIELD_NAMES)):
            problems.append(f"intervals.fields must name exactly {list(FIELD_NAMES)}")
        if "columns" in block and (not isinstance(block["columns"], dict) or set(block["columns"]) != set(COLUMN_NAMES)):
            problems.append(f"intervals.columns must name exactly {list(COLUMN_NAMES)}")
        if "model" in block:
            model = block["model"]
            if not isinstance(model, dict) or model.get("kind", "cell") != "cell":
                problems.append("intervals.model must be a cell column spec")
            else:
                problems += cls._column_problems({"name": "model", "kind": "cell", **model})
        return problems

    @classmethod
    def _column_problems(cls, column):
        if not isinstance(column, dict) or set(column) - COLUMN_KEYS:
            return [f"column {column!r} has unknown keys"]
        name = column.get("name", "?")
        problems = cls._source_problems(f"column {name}", column.get("source"))
        if column.get("kind") not in COLUMN_KINDS:
            problems.append(f"column {name}: kind {column.get('kind')!r} is not registered")
        if column.get("kind") == "cell" and not column.get("field"):
            problems.append(f"column {name}: cell needs a field")
        if not isinstance(column.get("name"), str) or not column.get("name"):
            problems.append("every column needs a name")
        if "where" in column and not isinstance(column["where"], dict):
            problems.append(f"column {name}: where must be an object")
        return problems

    def _lanes(self):
        try:
            with open(self.spec["batch"], encoding=ENCODING) as handle:
                lanes = json.load(handle)["lanes"]
        except (OSError, ValueError, KeyError, TypeError) as err:
            raise RollupError(f"batch {self.spec['batch']} unreadable: {err!r}")
        bad = [x for x in lanes if not isinstance(x, dict) or not isinstance(x.get("lane"), str)
               or not isinstance(x.get("status", STATUS_PENDING), str)
               or not isinstance(x.get("work_dir", ""), str)] if isinstance(lanes, list) else [lanes]
        names = [x["lane"] for x in lanes if x not in bad]
        if bad or len(set(names)) != len(names):
            raise RollupError(f"batch {self.spec['batch']} has malformed or duplicate lane entries")
        return lanes

    def _column_names(self):
        """Return the output column names in order."""
        names = [LANE_COLUMN, STATUS_COLUMN] + [c["name"] for c in self.spec["columns"]]
        names.append(self.interval["columns"]["model"])
        for block in self.blocks:
            names += list(self.names[block].values())
        return names + [VERDICT_COLUMN, FAILURES_COLUMN]

    def _cell_of(self, column, lane, work_dir, notes):
        """Return one declared cell; a problem becomes a note and None."""
        rows, problem = _Source(column["source"]).rows(work_dir, lane)
        if rows is None:
            notes.append(f"{column['name']}: {problem}")
            return None
        kind = COLUMN_KINDS[column.get("kind", "cell")]
        value = kind.value(rows, column, lane)
        if value is None:
            notes.append(f"{column['name']}: missing")
        return value

    def _values(self, lane, work_dir, notes):
        """Return the declared value columns of one lane."""
        return {c["name"]: self._cell_of(c, lane, work_dir, notes) for c in self.spec["columns"]}

    def _intervals(self, lane, work_dir, model, notes):
        """Return {block: (point, lo, hi)} of the chosen model; ambiguity is a note."""
        rows, problem = _Source(self.interval["source"]).rows(work_dir, lane)
        if rows is None:
            notes.append(f"intervals: {problem}")
            return {}
        fields, found, seen = self.interval["fields"], {}, {}
        for row in rows:
            block = _number(row.get(fields["block"]))
            if (row.get(fields["model"]) == model and block in self.blocks
                    and row.get(fields["reference"]) == self.interval["reference"]
                    and _matches(row, self.interval["where"], lane)):
                seen[block] = seen.get(block, 0) + 1
                found[block] = tuple(_number(row.get(fields[k])) for k in ("point", "lo", "hi"))
        for block in self.blocks:
            if block in found and found[block][1] is None:
                notes.append(f"intervals: no finite lower bound at block {_block_text(block)}")
        for block, count in sorted(seen.items()):
            if count > 1:
                notes.append(f"intervals: {count} rows for {model} at block {_block_text(block)}")
                found.pop(block)
        return found

    def _verdict(self, status, found, flawed):
        if status == STATUS_PENDING:
            return PENDING
        if status != STATUS_OK:
            return FAILED
        if flawed or any(b not in found or found[b][1] is None for b in self.blocks):
            return UNSCORED
        return ACCEPTED if all(found[b][1] > self.threshold for b in self.blocks) else REJECTED

    def _row(self, entry):
        lane, status = entry["lane"], entry.get("status", STATUS_PENDING)
        work_dir, notes = entry.get("work_dir", ""), []
        if status not in (STATUS_OK, STATUS_PENDING):
            where = f" at {entry['halting_step']}" if entry.get("halting_step") else ""
            notes.append(f"{status}{where}, exit {entry.get('exit_code')}")
        row = {LANE_COLUMN: lane, STATUS_COLUMN: status}
        model, found = None, {}
        if status == STATUS_OK:
            row.update(self._values(lane, work_dir, notes))
            model = self._cell_of({"name": "model", "kind": "cell", **self.interval["model"]}, lane, work_dir, notes)
            found = self._intervals(lane, work_dir, model, notes) if model is not None else {}
        row[self.interval["columns"]["model"]] = model
        for block in self.blocks:
            names = self.names[block]
            point, lo, hi = found.get(block, (None, None, None))
            row[names["skill"]], row[names["lo"]], row[names["hi"]] = point, lo, hi
        row[VERDICT_COLUMN] = self._verdict(status, found, bool(notes))
        row[FAILURES_COLUMN] = "; ".join(notes)
        return row

    def table(self):
        """Return (columns, rows): one row dict per batch lane, in batch order.

        Returns
        -------
        tuple
            The output column names and the list of row dicts.

        Raises
        ------
        RollupError
            If ``batch.json`` is unreadable or malformed.
        """
        return self._column_names(), [self._row(entry) for entry in self._lanes()]

    @staticmethod
    def _csv_text(value):
        """Return a cell's CSV text; text that a spreadsheet would run is neutralized."""
        if value is None:
            return ""
        if isinstance(value, str) and value.startswith(FORMULA_LEADS) and _number(value) is None:
            return "'" + value
        return value

    @classmethod
    def _csv_bytes(cls, columns, rows):
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow([cls._csv_text(row.get(c)) for c in columns])
        return buffer.getvalue().encode(ENCODING)

    @staticmethod
    def _cell(column, row):
        value = row.get(column)
        text = MISSING_TEXT if value is None or value == "" else str(value)
        css = f' class="{html.escape(text)}"' if column == VERDICT_COLUMN else ""
        return f"<td{css}>{html.escape(text)}</td>"

    def _html_bytes(self, columns, rows):
        head = "".join(f"<th>{html.escape(c)}</th>" for c in columns)
        body = "".join(
            "<tr>" + "".join(self._cell(c, r) for c in columns) + "</tr>" for r in rows
        )
        notes = self.spec.get("notes")
        intro = f"<p>{html.escape(notes)}</p>" if notes else ""
        title = html.escape(self.spec["title"])
        blocks = [_block_text(b) for b in self.blocks]
        page = (
            f"<title>{title}</title><style>{PAGE_STYLE}</style><h1>{title}</h1>{intro}"
            f"<p>Verdict: accepted when the lower bound is above {self.threshold} "
            f"for every block in {html.escape(', '.join(blocks))}. Click a header to sort.</p>"
            f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
            f"<script>{SORT_SCRIPT}</script>"
        )
        return page.encode(ENCODING)

    def write(self):
        """Write ``rollup.csv`` and ``report.html``; return their paths.

        Returns
        -------
        tuple of str
            The CSV path and the HTML path.

        Raises
        ------
        RollupError
            If ``batch.json`` is unreadable or malformed.
        """
        columns, rows = self.table()
        out = self.spec["output_dir"]
        paths = (os.path.join(out, CSV_NAME), os.path.join(out, HTML_NAME))
        land_bytes(paths[0], self._csv_bytes(columns, rows))
        land_bytes(paths[1], self._html_bytes(columns, rows))
        return paths


def _block_text(block):
    """Return a block size as text without a trailing ``.0``."""
    return str(int(block)) if float(block).is_integer() else str(block)


def main(argv=None, out=print):
    """Command-line entry: ``python -m dskit.pipeline.workflow_rollup <spec.json>``.

    Parameters
    ----------
    argv : list of str, optional
        Arguments; defaults to ``sys.argv``.
    out : callable, default print
        Receives each refusal line.

    Returns
    -------
    int
        0 wrote the files, 1 refused.
    """
    import argparse

    parser = argparse.ArgumentParser(prog="workflow_rollup")
    parser.add_argument("spec", help="path to the rollup spec JSON")
    args = parser.parse_args(argv)
    try:
        with open(args.spec, encoding=ENCODING) as handle:
            spec = json.load(handle)
        WorkflowRollup(spec).write()
    except RollupError as err:
        for problem in err.problems:
            out(f"refused: {problem}")
        return EXIT_ERROR
    except (OSError, ValueError) as err:
        out(f"refused: {err!r}")
        return EXIT_ERROR
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
