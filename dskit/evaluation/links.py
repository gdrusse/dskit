"""An ``ArtifactLinks`` node: show another run's outputs in a report, untouched (ADR-0283).

A report often has to point at evidence a different run wrote and that may
not exist yet. This node (role ``report``, by dotted path
``dskit.evaluation.links:ArtifactLinks``) takes the written paths of an
:class:`~dskit.evaluation.nodes.EvaluationReport` and lists each expected
artifact with its path, SHA-256 and byte count when the file is there, and
``pending`` when it is not: it never invents a hash or a size.

Read-only on the linked root. Files there are opened for reading to hash
them; a declared grid table (and a declared pins file) is read to report
status. Nothing under the root is created, changed or removed, and a report
directory inside the root (or the root inside the report directory), a
symlink resolving outside the root, and a CSV name that is not a bare
``.csv`` file name (or collides with a report file) are refused. It writes
only beside the report: first one marked block appended to ``summary.md``
and ``report.html`` (a rerun replaces that block, never stacks it), then
``<csv_name>`` (``artifact-links.csv``) and a cells CSV when a table is
declared.

Import cost: stdlib plus ``dskit.pipeline``.
"""

from __future__ import annotations

import csv
import hashlib
import html
import io
import itertools
import json
import os
import string

from dskit.pipeline.kinds_report import csv_text
from dskit.pipeline.node import Node, atomic_write, reject_unknown_params
from dskit.pipeline.runs import render_cell

__all__ = ["ArtifactLinks"]

_CHUNK = 1 << 20
_PENDING = "pending"
_BEGIN, _END = "artifact-links:begin", "artifact-links:end"
_COLUMNS = ("id", "key", "path", "status", "sha256", "bytes")
_CELL_COLUMNS = ("case", "unit", "status")
_CHECK_COLUMNS = ("check", "status", "found", "expected", "cases", "pinned")


def _relative_problem(label, value):
    """Return why ``value`` is not a plain relative path, or None."""
    if not isinstance(value, str) or not value:
        return f"{label} must be a non-empty string, got {value!r}"
    if os.path.isabs(value) or ".." in value.replace("\\", "/").split("/"):
        return f"{label} must be a relative path inside root, got {value!r}"
    return None


def _placeholder_problem(label, path):
    """Return why a ``{placeholder}`` in ``path`` is unusable, or None.

    Only plain ``{identifier}`` fields are allowed: no empty or numeric
    field, attribute or index, conversion or format spec.
    """
    try:
        fields = list(string.Formatter().parse(path))
    except ValueError as exc:
        return f"{label}: malformed path template {path!r}: {exc}"
    for _text, name, spec, conversion in fields:
        if name is None:
            continue
        if not name.isidentifier() or spec or conversion:
            return (f"{label}: placeholder {{{name}{'!' + conversion if conversion else ''}"
                    f"{':' + spec if spec else ''}}} must be a plain {{identifier}}")
    return None


def _inside(child, parent):
    """Report whether the resolved ``child`` is ``parent`` or below it."""
    child, parent = os.path.realpath(child), os.path.realpath(parent)
    return os.path.commonpath([child, parent]) == parent


class ArtifactLinks(Node):
    """List expected artifacts of another run in a rendered report (role ``report``).

    Parameters
    ----------
    params : dict
        ``root`` (str, REQUIRED) — the linked directory. ``files`` (list,
        REQUIRED) — each ``{id, path, axes?}``: ``path`` is relative to
        ``root`` and may hold ``{axis}`` placeholders, one per key of
        ``axes`` (``{axis: [values]}``), expanded as a product in the order
        written. ``pins`` (object, optional) — ``{file, files_key}``: a JSON
        file whose ``files_key`` mapping holds ``{relative path: {sha256,
        bytes}}``; each link then carries ``pinned`` (``match``,
        ``mismatch``, ``unpinned``, ``n/a`` for the pins file itself,
        ``pending`` while the pins file or the link is absent). ``table``
        (object, optional) — a CSV grid read for status: ``file``, ``where``
        (``{column: value}`` row filter), ``case`` and ``unit`` (column
        names), ``cases`` and ``units`` (the expected values), ``ok``
        (``{column, equals}``) and ``checks`` (``{name: [cases]}``).
        ``title`` (str) heads the block; ``csv_name`` (str, a bare
        ``<name>.csv``) names the CSV, written beside the report only.

    Inputs
    ------
    ``paths`` (REQUIRED)
        The ``EvaluationReport`` ``paths`` output (``summary`` and ``html``).

    Outputs
    -------
    ``rows``
        One mapping per link over id, key, path, status, sha256, bytes (and
        ``pinned``); ``status`` is ``present`` or ``pending``.
    ``cells``
        One mapping per expected (case, unit): ``ok``, ``not ok (<value>)``,
        ``missing`` or ``pending``.
    ``checks``
        One mapping per named check, with ``pinned`` (the table file's pin
        status, ``n/a`` without ``pins``). ``complete`` when every cell of its
        cases is ok (and, with ``pins``, the pins file confirms the table
        file); ``incomplete``; ``unverified`` when the pins file contradicts
        or omits the table file; ``pending`` while the table or the pins
        file is absent.
    ``paths``
        ``{csv, cells_csv}`` as written beside the report.

    Examples
    --------
    ::

        node = ArtifactLinks("links", {
            "root": "/data/other-run",
            "files": [{"id": "report", "path": "report.md"},
                      {"id": "daybook", "path": "{case}/{unit}.md",
                       "axes": {"case": ["a", "b"], "unit": ["x"]}}]})
        out = node.run(ctx, {"paths": report_paths})
        out["rows"][0]["status"]   # 'present' or 'pending'
    """

    role = "report"
    outputs = ("rows", "cells", "checks", "paths")
    _PARAMS = ("root", "files", "pins", "table", "title", "csv_name", "notes")

    @classmethod
    def validate_params(cls, params):
        """Refuse unknown knobs, an unusable root, file, pins or table declaration.

        Parameters
        ----------
        params : dict

        Returns
        -------
        list of str
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        root = params.get("root")
        if not isinstance(root, str) or not root:
            problems.append(f"root must be a non-empty string, got {root!r}")
        problems.extend(cls._file_problems(params.get("files")))
        if "pins" in params:
            problems.extend(cls._pins_problems(params["pins"]))
        if "table" in params:
            problems.extend(cls._table_problems(params["table"]))
        if "title" in params and (not isinstance(params["title"], str) or not params["title"]):
            problems.append(f"title must be a non-empty string, got {params['title']!r}")
        if "csv_name" in params:
            name = params["csv_name"]
            if (not isinstance(name, str) or os.path.basename(name) != name or "\\" in name
                    or name in ("", ".", "..") or not name.endswith(".csv")):
                problems.append(f"csv_name must be a bare '<name>.csv' file name (it is written "
                                f"beside the report, never elsewhere), got {name!r}")
        return problems

    @staticmethod
    def _file_problems(files):
        """Problems with the ``files`` declaration."""
        if not isinstance(files, list) or not files:
            return [f"files must be a non-empty list, got {files!r}"]
        problems, seen = [], set()
        for index, entry in enumerate(files):
            label = f"files[{index}]"
            if not isinstance(entry, dict) or set(entry) - {"id", "path", "axes"}:
                problems.append(f"{label} must be an object with id, path and optional axes")
                continue
            if not isinstance(entry.get("id"), str) or not entry["id"]:
                problems.append(f"{label}.id must be a non-empty string")
            elif entry["id"] in seen:
                problems.append(f"{label}.id {entry['id']!r} is declared twice")
            seen.add(entry.get("id"))
            problem = _relative_problem(f"{label}.path", entry.get("path"))
            if problem:
                problems.append(problem)
                continue
            problem = _placeholder_problem(label, entry["path"])
            if problem:
                problems.append(problem)
                continue
            axes = entry.get("axes", {})
            named = {name for _t, name, _s, _c in string.Formatter().parse(entry["path"]) if name}
            if not isinstance(axes, dict) or named != set(axes):
                problems.append(f"{label}: the path's {{placeholders}} {sorted(named)} must "
                                f"match the axes keys {sorted(axes) if isinstance(axes, dict) else axes!r}")
                continue
            for axis, values in axes.items():
                bad = (not isinstance(values, list) or not values
                       or any(not isinstance(v, str) or not v or "/" in v or "\\" in v or v in (".", "..")
                              for v in values))
                if bad:
                    problems.append(f"{label}.axes.{axis} must be a non-empty list of plain strings")
        return problems

    @staticmethod
    def _pins_problems(pins):
        """Problems with the ``pins`` declaration."""
        if not isinstance(pins, dict) or set(pins) != {"file", "files_key"}:
            return ["pins must be an object with exactly file and files_key"]
        problems = []
        problem = _relative_problem("pins.file", pins.get("file"))
        if problem:
            problems.append(problem)
        if not isinstance(pins.get("files_key"), str) or not pins["files_key"]:
            problems.append("pins.files_key must be a non-empty string")
        return problems

    @staticmethod
    def _table_problems(table):
        """Problems with the ``table`` declaration."""
        need = {"file", "case", "unit", "cases", "units", "ok", "checks"}
        if not isinstance(table, dict) or not need <= set(table) or set(table) - need - {"where"}:
            return [f"table must be an object with {sorted(need)} and optional where"]
        problems = []
        problem = _relative_problem("table.file", table["file"])
        if problem:
            problems.append(problem)
        for name in ("case", "unit"):
            if not isinstance(table[name], str) or not table[name]:
                problems.append(f"table.{name} must be a column name")
        for name in ("cases", "units"):
            values = table[name]
            if not isinstance(values, list) or not values or any(
                    not isinstance(v, str) for v in values):
                problems.append(f"table.{name} must be a non-empty list of strings")
        if not isinstance(table.get("where", {}), dict):
            problems.append("table.where must be an object of column: value")
        ok = table["ok"]
        if not isinstance(ok, dict) or set(ok) != {"column", "equals"}:
            problems.append("table.ok must be {column, equals}")
        checks = table["checks"]
        if not isinstance(checks, dict) or not checks:
            problems.append("table.checks must be a non-empty object of name: [cases]")
        elif isinstance(table["cases"], list):
            for name, cases in checks.items():
                if (not isinstance(cases, list) or not cases
                        or any(c not in table["cases"] for c in cases)):
                    problems.append(f"table.checks.{name} must list cases from table.cases")
        return problems

    def validate_inputs(self, inputs):
        """Refuse a ``paths`` input without the report's ``summary`` and ``html`` files.

        Parameters
        ----------
        inputs : dict

        Returns
        -------
        list of str
        """
        paths = inputs.get("paths")
        if not isinstance(paths, dict) or not all(
                isinstance(paths.get(name), str) for name in ("summary", "html")):
            return ["paths must be the report's {summary, html, ...} file paths"]
        return []

    def run(self, ctx, inputs):
        """Hash what exists, read the declared status table, write beside the report.

        Parameters
        ----------
        ctx : NodeContext or None
        inputs : dict

        Returns
        -------
        dict
            ``rows``, ``cells``, ``checks`` and ``paths``.

        Raises
        ------
        ValueError
            When the report directory and the linked root contain one
            another, a linked path (or its symlink target) lies outside the
            root, a CSV name collides with a report file, or a declared pins
            file or table cannot be read.
        """
        problems = self.validate_inputs(inputs)
        if problems:
            raise ValueError(f"{self.key}: {problems[0]}")
        root = os.path.expanduser(self.params["root"])
        out_dir = os.path.dirname(os.path.abspath(inputs["paths"]["summary"]))
        if os.path.dirname(os.path.abspath(inputs["paths"]["html"])) != out_dir:
            raise ValueError(f"{self.key}: the report's summary and html must share a directory")
        if _inside(out_dir, root) or _inside(root, out_dir):
            raise ValueError(
                f"{self.key}: the report directory {out_dir!r} and the linked root {root!r} are "
                "inside one another; the node writes only beside the report, never into the root")
        targets = self._csv_targets(out_dir, root, inputs["paths"])
        self._files = {}
        pins = self._pins(root)
        rows = self._rows(root, pins)
        cells, checks = self._grid(root, pins)
        columns = _COLUMNS + (("pinned",) if "pins" in self.params else ())
        paths = self._write(targets, inputs["paths"], columns, rows, cells, checks)
        return {"rows": rows, "cells": cells, "checks": checks, "paths": paths}

    def _csv_targets(self, out_dir, root, report_paths):
        """Return ``{csv, cells_csv}`` paths, each directly inside the report directory.

        Refused when a name would resolve elsewhere, inside the linked root,
        or onto one of the report's own files.
        """
        csv_path = os.path.join(out_dir, self.params.get("csv_name", "artifact-links.csv"))
        targets = {"csv": csv_path,
                   "cells_csv": os.path.splitext(csv_path)[0] + "-cells.csv"}
        taken = {os.path.basename(path) for path in report_paths.values()}
        for name, path in targets.items():
            if (os.path.dirname(os.path.realpath(path)) != os.path.realpath(out_dir)
                    or _inside(path, root)):
                raise ValueError(f"{self.key}: {name} {path!r} must sit directly in the report "
                                 f"directory {out_dir!r}, outside the linked root")
            if os.path.basename(path) in taken:
                raise ValueError(f"{self.key}: {name} {os.path.basename(path)!r} collides with "
                                 "a file the report already wrote")
        return targets

    def _targets(self):
        """Yield ``(id, key, relative path)`` for every declared link, axes expanded."""
        for entry in self.params["files"]:
            axes = entry.get("axes", {})
            for values in itertools.product(*axes.values()):
                chosen = dict(zip(axes, values))
                yield (entry["id"], ",".join(f"{a}={v}" for a, v in chosen.items()),
                       entry["path"].format(**chosen))

    def _locate(self, root, relative):
        """Return ``(path, present)`` for one file under ``root``.

        A broken symlink or missing file is absent (``pending``); a symlink
        resolving outside the root, or a path that is not a file, is refused.
        """
        path = os.path.join(root, relative)
        if not _inside(path, root):
            raise ValueError(f"{self.key}: {relative!r} resolves outside the linked root "
                             f"{root!r} (a symlink out of it is refused)")
        if os.path.isfile(path):
            return path, True
        if os.path.exists(path):
            raise ValueError(f"{self.key}: {path!r} exists but is not a file")
        return path, False

    def _load(self, root, relative):
        """Return one file's facts, read ONCE per run: ``{path, present, sha256, bytes, raw}``.

        The SHA-256 and the byte count are taken over the very bytes read, and
        ``raw`` keeps those bytes for the pins file and the table file only,
        so what is parsed is exactly what is hashed and compared to the pin.
        (``workflow.path_hash`` hashes by path, a second read; it is not used
        for that reason.) Every other file is hashed in chunks and dropped.
        """
        if relative in self._files:
            return self._files[relative]
        path, present = self._locate(root, relative)
        found = {"path": path, "present": present, "sha256": None, "bytes": None, "raw": None}
        if present:
            keep = relative in (self.params.get("pins", {}).get("file"),
                                self.params.get("table", {}).get("file"))
            sha, size, kept = hashlib.sha256(), 0, []
            with open(path, "rb") as stream:
                for chunk in iter(lambda: stream.read(_CHUNK), b""):
                    sha.update(chunk)
                    size += len(chunk)
                    if keep:
                        kept.append(chunk)
            found.update(sha256=sha.hexdigest(), bytes=size, raw=b"".join(kept) if keep else None)
        self._files[relative] = found
        return found

    def _pins(self, root):
        """Return the pins mapping, or None when none is declared or the pins file is absent."""
        declared = self.params.get("pins")
        if declared is None:
            return None
        loaded = self._load(root, declared["file"])
        if not loaded["present"]:
            return None
        try:
            mapping = json.loads(loaded["raw"].decode("utf-8"))[declared["files_key"]]
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"{self.key}: cannot read pins {declared['file']!r}: {exc}") from exc
        if not isinstance(mapping, dict):
            raise ValueError(f"{self.key}: pins {declared['files_key']!r} is not a mapping")
        return mapping

    def _pinned(self, relative, present, digest, pins):
        """Return one file's pin status against the pins mapping."""
        if relative == self.params["pins"]["file"]:
            return "n/a"
        if pins is None or not present:
            return _PENDING
        entry = pins.get(relative)
        if not isinstance(entry, dict):
            return "unpinned"
        return "match" if (entry.get("sha256"), entry.get("bytes")) == digest else "mismatch"

    def _rows(self, root, pins):
        """Return one row per link: its path, status and, only when present, hash and size."""
        rows = []
        for ident, key, relative in self._targets():
            loaded = self._load(root, relative)
            row = {"id": ident, "key": key, "path": loaded["path"],
                   "status": "present" if loaded["present"] else _PENDING,
                   "sha256": loaded["sha256"], "bytes": loaded["bytes"]}
            if "pins" in self.params:
                row["pinned"] = self._pin_of(relative, loaded, pins)
            rows.append(row)
        return rows

    def _pin_of(self, relative, loaded, pins):
        """Return a file's pin status from its single read: rows and checks share it."""
        return self._pinned(relative, loaded["present"], (loaded["sha256"], loaded["bytes"]), pins)

    def _grid(self, root, pins):
        """Return ``(cells, checks)`` from the declared table, or two empty lists.

        The cells are parsed from the same bytes whose digest is compared to
        the pin. With ``pins`` declared a check is ``complete`` only when the
        pins file exists and confirms those bytes; a mismatch or an unpinned
        table is ``unverified`` and an absent pins file is ``pending``.
        """
        table = self.params.get("table")
        if table is None:
            return [], []
        loaded = self._load(root, table["file"])
        exists = loaded["present"]
        found = self._read_table(loaded["raw"], table) if exists else {}
        pinned = self._pin_of(table["file"], loaded, pins) if "pins" in self.params else "n/a"
        cells = [{"case": case, "unit": unit,
                  "status": self._cell(table, exists, found.get((case, unit)))}
                 for case in table["cases"] for unit in table["units"]]
        state = {(c["case"], c["unit"]): c["status"] for c in cells}
        checks = []
        for name, cases in table["checks"].items():
            wanted = [state[case, unit] for case in cases for unit in table["units"]]
            ok = sum(1 for status in wanted if status == "ok")
            checks.append({
                "check": name, "found": ok, "expected": len(wanted), "cases": ",".join(cases),
                "pinned": pinned,
                "status": self._check_status(exists, pinned, ok == len(wanted))})
        return cells, checks

    @staticmethod
    def _check_status(exists, pinned, every_cell_ok):
        """Return ``pending``, ``unverified``, ``incomplete`` or ``complete`` for one check."""
        if not exists or pinned == _PENDING:
            return _PENDING
        if pinned in ("mismatch", "unpinned"):
            return "unverified"
        return "complete" if every_cell_ok else "incomplete"

    def _read_table(self, raw, table):
        """Return ``{(case, unit): [ok-column values]}`` over the rows passing ``where``."""
        found = {}
        try:
            for row in csv.DictReader(io.StringIO(raw.decode("utf-8"), newline="")):
                if all(row.get(col) == str(val) for col, val in table.get("where", {}).items()):
                    key = (row.get(table["case"]), row.get(table["unit"]))
                    found.setdefault(key, []).append(row.get(table["ok"]["column"]))
        except (ValueError, csv.Error) as exc:
            raise ValueError(f"{self.key}: cannot read table {table['file']!r}: {exc}") from exc
        return found

    @staticmethod
    def _cell(table, exists, values):
        """Return one expected cell's status from the values found for it."""
        if not exists:
            return _PENDING
        if not values:
            return "missing"
        bad = [v for v in values if v != str(table["ok"]["equals"])]
        return "ok" if not bad else f"not ok ({bad[0]})"

    def _block(self, columns, rows, cells, checks):
        """Return the ``(markdown lines, html)`` block for this manifest."""
        title = self.params.get("title", "Linked artifacts")
        root = self.params["root"]
        note = (f"Read-only links to files under {root}. A file not yet written is pending: "
                "no hash or size is shown for it.")
        parts = [("Links", columns, rows)]
        if checks:
            parts += [("Checks", _CHECK_COLUMNS, checks), ("Cells", _CELL_COLUMNS, cells)]
        lines = [f"<!-- {_BEGIN} -->", f"## {title}", "", note, ""]
        body = [f"<!--{_BEGIN}--><section id=\"artifact-links\"><h2>{html.escape(title)}</h2>"
                f"<p class=note>{html.escape(note)}</p>"]
        for heading, cols, data in parts:
            lines += [f"### {heading}", "",
                      "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
            lines += ["| " + " | ".join(render_cell(row.get(c)) for c in cols) + " |"
                      for row in data]
            lines.append("")
            body.append(f"<h3>{heading}</h3><div class=scroll><table><thead><tr>"
                        + "".join(f"<th>{c}</th>" for c in cols) + "</tr></thead><tbody>"
                        + "".join("<tr>" + "".join(
                            f"<td>{html.escape(render_cell(row.get(c)))}</td>" for c in cols)
                            + "</tr>" for row in data)
                        + "</tbody></table></div>")
        lines.append(f"<!-- {_END} -->")
        body.append(f"</section><!--{_END}-->")
        return lines, "".join(body)

    @staticmethod
    def _spliced(text, block, anchor):
        """Return ``text`` with its marked block replaced, or ``block`` added at ``anchor``."""
        begin, end = text.find(f"<!--{_BEGIN}-->"), text.find(f"<!--{_END}-->")
        if begin == -1:
            begin, end = text.find(f"<!-- {_BEGIN} -->"), text.find(f"<!-- {_END} -->")
        if begin != -1 and end != -1:
            close = text.index("-->", end) + 3
            text = text[:begin] + text[close:].lstrip("\n")
        if anchor is None:
            return text.rstrip() + "\n\n" + block + "\n"
        at = text.find(anchor)
        if at == -1:
            raise ValueError(f"cannot place the block: {anchor!r} not found")
        return text[:at] + block + text[at:]

    def _write(self, targets, report_paths, columns, rows, cells, checks):
        """Splice the block into the report files, then write the CSVs; return the CSV paths.

        The CSVs go last: a failure earlier leaves no CSV that points at a
        report without the block.
        """
        lines, markup = self._block(columns, rows, cells, checks)
        summary_path, html_path = report_paths["summary"], report_paths["html"]
        with open(summary_path, encoding="utf-8") as stream:
            summary = self._spliced(stream.read(), "\n".join(lines), None)
        with open(html_path, encoding="utf-8") as stream:
            page = self._spliced(stream.read(), markup, "</main>")
        atomic_write(summary_path, summary.encode("utf-8"))
        atomic_write(html_path, page.encode("utf-8"))
        paths = {"csv": targets["csv"]}
        atomic_write(targets["csv"], csv_text(columns, rows).encode("utf-8"))
        if checks:
            paths["cells_csv"] = targets["cells_csv"]
            atomic_write(targets["cells_csv"], csv_text(_CELL_COLUMNS, cells).encode("utf-8"))
        return paths
