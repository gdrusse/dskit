"""Assemble a verified report from a workflow's declared outputs (ADR-0227).

A workflow (:mod:`dskit.pipeline.workflow`) records, per step, the exit code,
the config hash and a content hash of every declared output in ``workflow.json``.
This module is the last link: it reads a spec naming the ledger and the sections
to show, VERIFIES before it writes anything, and then emits ``report.md``, a
self-contained ``report.html`` and one ``sections/<name>.csv`` per section.

Verification is the point. The report is refused (exit 1, nothing written) when
the ledger is missing or unreadable, a required step is absent or did not exit
0, a section's source is missing or yields no rows, or a section bound to a
ledger output no longer hashes to what the ledger recorded. A table cell with no
value prints ``n/a`` (for example a loss a non-trained model never produces)
rather than a blank, so a gap is visible. Nothing project-specific lives here:
section names, paths, statements and limits all come from the spec.

Readers are named strategy objects in :data:`READERS`; a project adds a format
(parquet, through its library pack) by subclassing :class:`Reader` and calling
:func:`register_reader`. Stdlib only.

Run it as a workflow step with ``python -m dskit.pipeline.workflow_report
<spec.json>``; exit 0 wrote the report, 1 refused.
"""

from __future__ import annotations

import csv
import html
import io
import json
import os
import sys
from abc import ABC, abstractmethod

from dskit.pipeline.node import atomic_write
from dskit.pipeline.workflow import EXIT_ERROR, EXIT_OK, LEDGER_NAME, path_hash

__all__ = [
    "ReportError",
    "Reader",
    "JsonReader",
    "JsonLinesReader",
    "CsvReader",
    "READERS",
    "register_reader",
    "row_matches",
    "WorkflowReport",
    "main",
]

ENCODING = "utf-8"
MISSING_TEXT = "n/a"
DEFAULT_MAX_ROWS = 200
SECTIONS_DIR = "sections"
MARKDOWN_NAME = "report.md"
HTML_NAME = "report.html"
KEY_COLUMN = "key"
VALUE_COLUMN = "value"
PATH_SEP = "."
TOP_KEYS = {
    "ledger", "output_dir", "title", "required_steps", "sections",
    "statements", "max_rows", "notes", "lane",
}
LANE_KEYS = {"field", "value"}
FLATTEN_KEYS = {"separator", "max_items"}
SECTION_KEYS = {
    "name", "title", "source", "format", "rows", "step", "output", "bound",
    "required", "notes", "caption", "flatten", "where",
}
STATEMENT_KEYS = {"title", "text"}
STYLE = (
    "body{font:14px system-ui,sans-serif;margin:0 auto;max-width:72rem;"
    "padding:1rem 1.25rem;background:#fff;color:#111}"
    "table{border-collapse:collapse;display:block;overflow-x:auto;margin:.5rem 0}"
    "th,td{border:1px solid #ccc;padding:.25rem .5rem;text-align:left}"
    "th{background:#f0f0f0}code{font-size:12px}"
    "@media(prefers-color-scheme:dark){body{background:#111;color:#eee}"
    "th{background:#222}th,td{border-color:#444}}"
)


class ReportError(Exception):
    """A refused report: ``problems`` lists every reason, all shown at once."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


def _flatten(value, prefix=""):
    """Return (dotted key, scalar) pairs of a nested JSON object."""
    if isinstance(value, dict):
        pairs = []
        for key in value:
            pairs += _flatten(value[key], f"{prefix}{key}{PATH_SEP}")
        return pairs
    return [(prefix[: -len(PATH_SEP)], value)]


def _descend(value, dotted):
    """Follow a dotted path of dict keys; None when a step is absent."""
    for part in dotted.split(PATH_SEP) if dotted else ():
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _flat_row(row, separator, max_items):
    """Return a row with nested objects as dotted columns and short scalar lists joined."""
    flat = {}
    for key, value in row.items():
        if isinstance(value, dict) and value:
            for sub, leaf in _flatten(value, f"{key}{PATH_SEP}"):
                flat[sub] = _flat_leaf(leaf, separator, max_items)
        else:
            flat[key] = _flat_leaf(value, separator, max_items)
    return flat


def _flat_leaf(value, separator, max_items):
    """Join a short list of scalars with ``separator``; anything else stays as is."""
    scalar = not any(isinstance(x, (dict, list)) for x in value) if isinstance(value, list) else False
    if scalar and len(value) <= max_items:
        return separator.join(str(x) for x in value)
    return value


def _cell(value):
    """Return a table cell's text: scalars as text, containers as compact JSON."""
    if value is None or value == "":
        return MISSING_TEXT
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return str(value)


def row_matches(row, where):
    """Say whether every ``where`` column of ``row`` reads as its value (both as table text).

    Parameters
    ----------
    row : dict
        One table row.
    where : dict or None
        Column -> wanted value; None or empty matches every row.

    Returns
    -------
    bool
    """
    return all(_cell(row.get(k)) == _cell(v) for k, v in (where or {}).items())


class Reader(ABC):
    """Turns one source file into table rows (a list of dicts).

    Examples
    --------
    Add a format by subclassing and registering it::

        class TextReader(Reader):
            def read(self, path, section):
                with open(path, encoding="utf-8") as handle:
                    return [{"line": x.rstrip()} for x in handle]

        register_reader("text", TextReader())
    """

    @abstractmethod
    def read(self, path, section):
        """Return the section's rows.

        Parameters
        ----------
        path : str
            The existing source file.
        section : dict
            The section spec (``rows`` may name a path inside the document).

        Returns
        -------
        list of dict
            One dict per row.

        Raises
        ------
        ValueError
            If the file cannot be parsed.
        """


class JsonReader(Reader):
    """A JSON document: a list of objects, or one object shown as key/value rows."""

    def read(self, path, section):
        """Return rows from the document, or from the list at ``section["rows"]``."""
        with open(path, encoding=ENCODING) as handle:
            document = json.load(handle)
        picked = _descend(document, section.get("rows", ""))
        if isinstance(picked, list):
            return [r if isinstance(r, dict) else {VALUE_COLUMN: r} for r in picked]
        if isinstance(picked, dict):
            return [{KEY_COLUMN: k, VALUE_COLUMN: v} for k, v in _flatten(picked)]
        return []


class JsonLinesReader(Reader):
    """One JSON object per non-blank line."""

    def read(self, path, section):
        """Return one row per line."""
        with open(path, encoding=ENCODING) as handle:
            return [json.loads(line) for line in handle if line.strip()]


class CsvReader(Reader):
    """A CSV file with a header row."""

    def read(self, path, section):
        """Return one row per record, keyed by the header."""
        with open(path, encoding=ENCODING, newline="") as handle:
            return list(csv.DictReader(handle))


READERS = {"json": JsonReader(), "jsonl": JsonLinesReader(), "csv": CsvReader()}


def register_reader(name, reader):
    """Register a :class:`Reader` under a format name.

    Parameters
    ----------
    name : str
        The ``format`` value a section uses.
    reader : Reader
        The strategy object.

    Raises
    ------
    ValueError
        If ``reader`` is not a :class:`Reader` or the name is taken.
    """
    if not isinstance(reader, Reader) or name in READERS:
        raise ValueError(f"reader {name!r} must be a new Reader instance")
    READERS[name] = reader


def _columns(rows):
    """Return the union of row keys in first-seen order."""
    seen = {}
    for row in rows:
        for key in row:
            seen.setdefault(key, None)
    return list(seen)


class WorkflowReport:
    """Verify a workflow's outputs against its ledger, then write the report.

    Parameters
    ----------
    spec : dict
        ``ledger`` (path to ``workflow.json`` or its directory), ``output_dir``,
        ``title``, ``sections`` (each: ``name``, ``title``, ``source``,
        ``format``, optional ``rows`` path, ``step`` + ``output`` to bind the
        source to a ledger hash, optional ``bound`` path when the ledger output
        is a directory holding ``source``, ``required`` default true),
        optional ``required_steps``, ``statements`` (``title``, ``text``),
        ``max_rows`` and ``notes``. A section may add ``caption`` (text shown under
        its title) and ``flatten`` (``separator``, ``max_items``: nested objects
        become dotted columns, scalar lists of at most ``max_items`` are joined)
        and ``where`` (top-level column -> text or number: only the rows whose
        cells read the same, compared as table text, are kept; none left refuses
        as an empty source does). Top-level ``lane`` (``field``, ``value``) keeps
        only the ledger rows whose ``field`` equals ``value`` in the provenance
        table.

    Examples
    --------
    Report a finished workflow::

        report = WorkflowReport({
            "ledger": "work", "output_dir": "work/report", "title": "Study",
            "sections": [{"name": "folds", "title": "Folds",
                          "source": "work/folds.jsonl", "format": "jsonl",
                          "step": "cut", "output": "folds"}],
        })
        report.write()
    """

    def __init__(self, spec):
        self.spec = spec
        problems = self._spec_problems()
        if problems:
            raise ReportError(problems)
        self.max_rows = spec.get("max_rows", DEFAULT_MAX_ROWS)
        self.ledger = None
        self.tables = {}

    def _spec_problems(self):
        spec = self.spec
        if not isinstance(spec, dict):
            return ["spec must be a JSON object"]
        problems = [f"unknown spec key {k!r}" for k in sorted(set(spec) - TOP_KEYS)]
        for key in ("ledger", "output_dir", "title", "sections"):
            if key not in spec:
                problems.append(f"spec key {key!r} is required")
        sections = spec.get("sections", [])
        if not isinstance(sections, list) or not sections:
            problems.append("sections must be a non-empty list")
            sections = []
        names = [s.get("name") for s in sections if isinstance(s, dict)]
        if len(set(names)) != len(names):
            problems.append("section names must be unique")
        for section in sections:
            problems += self._section_problems(section)
        problems += self._lane_problems(spec.get("lane"))
        for statement in spec.get("statements", []):
            if not isinstance(statement, dict) or set(statement) - STATEMENT_KEYS - {"notes"}:
                problems.append(f"statement {statement!r} needs only title and text")
        return problems

    @staticmethod
    def _section_problems(section):
        if not isinstance(section, dict):
            return [f"section {section!r} must be an object"]
        name = section.get("name", "?")
        problems = [f"section {name}: unknown key {k!r}"
                    for k in sorted(set(section) - SECTION_KEYS)]
        for key in ("name", "title", "source", "format"):
            if not isinstance(section.get(key), str) or not section.get(key):
                problems.append(f"section {name}: {key} is required text")
        if section.get("format") not in READERS:
            problems.append(f"section {name}: format {section.get('format')!r} has no reader")
        if ("step" in section) != ("output" in section):
            problems.append(f"section {name}: step and output come together")
        problems += WorkflowReport._flatten_problems(name, section.get("flatten"))
        problems += WorkflowReport._where_problems(name, section.get("where"))
        if "bound" in section and "output" not in section:
            problems.append(f"section {name}: bound needs step and output")
        return problems

    @staticmethod
    def _flatten_problems(name, flatten):
        if flatten is None:
            return []
        if (not isinstance(flatten, dict) or set(flatten) != FLATTEN_KEYS
                or not isinstance(flatten["separator"], str)
                or not isinstance(flatten["max_items"], int)
                or isinstance(flatten["max_items"], bool) or flatten["max_items"] < 0):
            return [f"section {name}: flatten needs exactly separator (text) and max_items (int >= 0)"]
        return []

    @staticmethod
    def _where_problems(name, where):
        if where is None:
            return []
        if (not isinstance(where, dict) or not where
                or not all(isinstance(k, str) and k for k in where)
                or not all(isinstance(v, (str, int, float)) and not isinstance(v, bool)
                           for v in where.values())):
            return [f"section {name}: where needs column names mapped to text or numbers"]
        return []

    @staticmethod
    def _lane_problems(lane):
        if lane is None:
            return []
        if (not isinstance(lane, dict) or set(lane) != LANE_KEYS
                or not all(isinstance(lane[k], str) and lane[k] for k in lane)):
            return ["lane needs exactly field and value, both non-empty text"]
        return []

    # -- verification --

    def _load_ledger(self):
        path = self.spec["ledger"]
        if os.path.isdir(path):
            path = os.path.join(path, LEDGER_NAME)
        if not os.path.isfile(path):
            raise ReportError([f"ledger {path} does not exist"])
        try:
            with open(path, encoding=ENCODING) as handle:
                return json.load(handle)["steps"]
        except (ValueError, KeyError) as err:
            raise ReportError([f"ledger {path} unreadable: {err!r}"])

    def _step_problems(self):
        wanted = self.spec.get("required_steps", list(self.ledger))
        problems = []
        for step in wanted:
            entry = self.ledger.get(step)
            if entry is None:
                problems.append(f"step {step} is not in the ledger")
            elif entry.get("exit_code") != EXIT_OK:
                problems.append(f"step {step} exit code {entry.get('exit_code')!r}, not {EXIT_OK}")
        return problems

    def _hash_problems(self, section):
        if "output" not in section:
            return []
        name, step, out = section["name"], section["step"], section["output"]
        recorded = (self.ledger.get(step) or {}).get("outputs", {}).get(out)
        actual = path_hash(section.get("bound", section["source"]))
        if recorded is None:
            return [f"section {name}: ledger has no hash for {step}.{out}"]
        if actual != recorded:
            return [f"section {name}: {step}.{out} hash {actual} differs from ledger {recorded}"]
        return []

    def _read(self, section):
        path = section["source"]
        if not os.path.isfile(path):
            return None, [f"section {section['name']}: source {path} is missing"]
        try:
            rows = READERS[section["format"]].read(path, section)
        except (ValueError, OSError) as err:
            return None, [f"section {section['name']}: {path} unreadable: {err!r}"]
        rows = [r for r in rows if row_matches(r, section.get("where"))]
        if not rows:
            return None, [f"section {section['name']}: source {path} yields no rows"]
        flatten = section.get("flatten")
        if flatten:
            rows = [_flat_row(r, flatten["separator"], flatten["max_items"]) for r in rows]
        return rows, []

    def verify(self):
        """Read the ledger and every section; return the problems found.

        Returns
        -------
        list of str
            Every refusal reason, empty when the report may be written.
        """
        self.ledger = self._load_ledger()
        problems = self._step_problems()
        for section in self.spec["sections"]:
            rows, found = self._read(section)
            if found:
                problems += found if section.get("required", True) else []
                continue
            bad = self._hash_problems(section)
            problems += bad
            if not bad:
                self.tables[section["name"]] = rows
        return problems

    # -- rendering --

    def _grid(self, name):
        rows = self.tables[name]
        columns = _columns(rows)
        return columns, [[_cell(row.get(c)) for c in columns] for row in rows]

    def _provenance(self):
        rows = []
        lane = self.spec.get("lane")
        for step, entry in self.ledger.items():
            if lane and entry.get(lane["field"]) != lane["value"]:
                continue
            outputs = entry.get("outputs", {})
            rows.append({
                "step": step, "exit_code": entry.get("exit_code"),
                "config_hash": entry.get("config_hash"),
                "rounds": entry.get("rounds"),
                "outputs": ", ".join(f"{k}={v[:12]}" for k, v in sorted(outputs.items())),
            })
        return rows

    def _blocks(self):
        """Return (title, rows-as-grid, note) triples in report order."""
        blocks = []
        provenance = self._provenance()
        columns = _columns(provenance)
        blocks.append(("Provenance (workflow ledger)", columns,
                       [[_cell(r.get(c)) for c in columns] for r in provenance], None))
        for section in self.spec["sections"]:
            if section["name"] in self.tables:
                head, body = self._grid(section["name"])
                note = " ".join(t for t in (section.get("notes"), section.get("caption")) if t)
                blocks.append((section["title"], head, body, note or None))
        return blocks

    def _markdown(self):
        lines = [f"# {self.spec['title']}", ""]
        for statement in self.spec.get("statements", []):
            lines += [f"**{statement['title']}** {statement['text']}", ""]
        for title, head, body, note in self._blocks():
            lines += [f"## {title}", ""] + ([note, ""] if note else [])
            lines += ["| " + " | ".join(head) + " |", "|" + " --- |" * len(head)]
            for row in body[: self.max_rows]:
                lines.append("| " + " | ".join(c.replace("|", "\\|") for c in row) + " |")
            if len(body) > self.max_rows:
                lines.append(f"\n{len(body) - self.max_rows} more row(s) in {SECTIONS_DIR}/ csv.")
            lines.append("")
        return "\n".join(lines)

    def _html(self):
        esc = html.escape
        parts = [f"<!doctype html><meta charset=utf-8><title>{esc(self.spec['title'])}</title>"
                 f"<style>{STYLE}</style><h1>{esc(self.spec['title'])}</h1>"]
        for statement in self.spec.get("statements", []):
            parts.append(f"<p><b>{esc(statement['title'])}</b> {esc(statement['text'])}</p>")
        for title, head, body, note in self._blocks():
            parts.append(f"<h2>{esc(title)}</h2>")
            parts.append(f"<p>{esc(note)}</p>" if note else "")
            parts.append("<table><tr>" + "".join(f"<th>{esc(h)}</th>" for h in head) + "</tr>")
            for row in body[: self.max_rows]:
                parts.append("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in row) + "</tr>")
            parts.append("</table>")
            if len(body) > self.max_rows:
                parts.append(f"<p>{len(body) - self.max_rows} more row(s) in the csv.</p>")
        return "".join(parts)

    def _csv(self, name):
        head, body = self._grid(name)
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(head)
        writer.writerows(body)
        return buffer.getvalue()

    def write(self):
        """Verify, then write ``report.md``, ``report.html`` and the section CSVs.

        Returns
        -------
        str
            The output directory.

        Raises
        ------
        ReportError
            If verification finds any problem; nothing is written then.
        """
        problems = self.verify()
        if problems:
            raise ReportError(problems)
        out = self.spec["output_dir"]
        os.makedirs(os.path.join(out, SECTIONS_DIR), exist_ok=True)
        atomic_write(os.path.join(out, MARKDOWN_NAME), self._markdown().encode(ENCODING))
        atomic_write(os.path.join(out, HTML_NAME), self._html().encode(ENCODING))
        for name in self.tables:
            atomic_write(os.path.join(out, SECTIONS_DIR, f"{name}.csv"),
                         self._csv(name).encode(ENCODING))
        return out


def main(argv=None, out=print):
    """Command line: ``workflow_report <spec.json>``; 0 wrote, 1 refused.

    Parameters
    ----------
    argv : list of str, optional
        Arguments after the program name (default ``sys.argv[1:]``).
    out : callable, default print
        Receives each message line.

    Returns
    -------
    int
        0 when the report was written, 1 when refused.
    """
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        out("usage: workflow_report <spec.json>")
        return EXIT_ERROR
    try:
        with open(argv[0], encoding=ENCODING) as handle:
            spec = json.load(handle)
        out(f"wrote {WorkflowReport(spec).write()}")
        return EXIT_OK
    except ReportError as err:
        for problem in err.problems:
            out(f"refused: {problem}")
    except (OSError, ValueError) as err:
        out(f"refused: {err!r}")
    return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
