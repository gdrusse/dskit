"""``table-file`` / ``table-write`` — a keyed table and a file, both ways.

Why this kind exists
--------------------
``concat``'s ``params.tables`` lets a document carry a keyed table as a literal,
and for a table a human can read that is exactly right: one venue's 29 fee rates
fit on a screen, and a reader approving the document can see every one of them.

Scale changes the answer. Another venue's fee book is 120 series across 268 dated
spans, because the venue's schedule genuinely changed several times inside the
run window. Inlined, it is six thousand lines in the middle of a document whose
first job is to be READ and approved. A document nobody can read is not a
document; it is a data file with a plan buried in it.

So the table moves to a file and the document NAMES it. What the document keeps
is the part a reader needs: where the data came from, when it was read, how many
entries it has, and — the load-bearing one — the digest of the exact bytes.

Why the digest is required, not optional
----------------------------------------
A bare path is a promise the document cannot keep. Two runs of the same document
against two different files are two different experiments that report the same
identity, and the difference is invisible: nothing downstream says which book was
on disk that day. The digest closes that. It is a param, so it is hash-material —
the run identity moves when the data moves — and it is VERIFIED on load, so a
file that has drifted from what the document approved refuses the run instead of
quietly pricing it. That is the same bargain ``concat`` makes by refusing an
overlapping key: the check that costs nothing when everything is fine is the one
that saves you when it is not.

``expect`` (the entry count) is the cheap second opinion: a digest proves the
bytes are the ones that were approved, a count proves they are the SHAPE that was
approved, and a reader can check the count against the note without hashing
anything.

Nothing here is fee-specific, or venue-specific, or even market-specific. A
settlement ledger, a category map, a strike table — any keyed lookup a document
needs and should not swallow whole — is the same shape.

The write half
--------------
:class:`TableWrite` is the counterpart, and it lives in this module on purpose:
what one writes, the other must be able to read back and verify. It emits a
``provenance`` block whose fields are EXACTLY :class:`TableFile`'s required
params (``path``, ``source``, ``retrieved``, ``sha256``, plus ``entries`` for
``expect``), so pinning a produced table into a later document is copying five
values, not re-deriving a digest by hand.

:class:`RecordsWrite` (ADR-0085) is its stream sibling — one canonical JSON
object per line, the digest of the exact bytes in ``metrics`` — and both are
members of :class:`FileWrite`, the abstract base that holds the write
discipline ONCE: a second copy of the no-clobber rule is where two writers
would drift apart. Three properties the family does not compromise on,
because writing into a store other processes share is the whole job:

* **It refuses to clobber.** An existing target is an error unless the document
  DECLARES ``overwrite``. Silent overwrite is the defect class that cost one
  project its settlement history (I-233); a writer that quietly replaces
  data is not a convenience.
* **It writes atomically.** Bytes go to a temp file in the same directory,
  are flushed and fsync'd, then ``os.replace``d into place. An interrupted run
  leaves either the old file or the new one — never a truncated file that
  parses as valid JSON and prices a portfolio.
* **It says what it wrote.** Path and byte count land in ``provenance`` and in
  the log line, so a run report can show the write rather than implying it.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from abc import abstractmethod

from dskit.pipeline.document import is_node_ref
from dskit.pipeline.kinds_stats import _reject_unknown
from dskit.pipeline.node import DEFAULT_NODE_KINDS, Node

__all__ = ["FileWrite", "RecordsWrite", "TableFile", "TableWrite", "register"]

#: A hex sha256, and nothing that merely looks like one.
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

#: ISO date, the form every provenance field in this repo already uses.
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: The canonical newline-JSON spelling — sorted keys, compact, ASCII: the
#: identity hash's own recipe minus its ``notes`` stripping (a record field
#: named ``notes`` is data here, never documentation). Two runs that
#: scored the same rows write byte-identical lines.
_LINE_SPELLING = {"sort_keys": True, "separators": (",", ":"), "ensure_ascii": True}

#: The table spelling: sorted keys, indented — a book a human reads.
_TABLE_SPELLING = {"sort_keys": True, "indent": 2}


class TableFile(Node):
    """Supply one keyed table from a declared file (role ``transform``).

    Params
    ------
    ``path`` (REQUIRED)
        Path to a JSON file whose top level is an object. A relative path is
        resolved from the process working directory — prefer one for a book
        that ships with the repo, so the document runs anywhere it is checked
        out. Absolute is accepted (``data_dir`` params already are), and the
        digest below is what makes either safe.
    ``source`` (REQUIRED)
        Where the data came from — a URL, or a written description of the
        endpoint and query. This is the field a reader checks first.
    ``retrieved`` (REQUIRED)
        ``YYYY-MM-DD``, the day it was read. A rate with no retrieval date
        cannot be audited against the venue's own history later.
    ``sha256`` (REQUIRED)
        Digest of the file's exact bytes, verified on load. See the module
        docstring: this is what makes the document's identity cover the data.
    ``expect``
        Expected number of top-level entries. Optional, and worth declaring:
        it is the one provenance check a human can verify by eye.

    Outputs
    -------
    ``table``
        The mapping, exactly as the file holds it.
    ``provenance``
        ``{path, source, retrieved, sha256, entries}`` — what was loaded and
        where it came from, carried into the run record so the answer survives
        the file being changed afterwards.
    """

    role = "transform"
    outputs = ("table", "provenance")

    _PARAMS = ("path", "source", "retrieved", "sha256", "expect")

    @classmethod
    def validate_params(cls, params):
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        path = params.get("path")
        if path is None:
            problems.append("path is required — name the file this table comes from")
        elif not is_node_ref(path) and (not isinstance(path, str) or not path):
            problems.append(f"path must be a non-empty string, got {path!r}")
        source = params.get("source")
        if not is_node_ref(source) and (not isinstance(source, str) or not source.strip()):
            problems.append(
                "source is required — a table with no stated origin is a set of "
                f"numbers somebody typed (got {source!r})"
            )
        retrieved = params.get("retrieved")
        if not is_node_ref(retrieved) and (
            not isinstance(retrieved, str) or not _ISO_DATE.match(retrieved or "")
        ):
            problems.append(
                "retrieved is required as YYYY-MM-DD — a value with no retrieval "
                f"date cannot be audited against the source later (got {retrieved!r})"
            )
        digest = params.get("sha256")
        if not is_node_ref(digest) and (
            not isinstance(digest, str) or not _SHA256.match((digest or "").lower())
        ):
            problems.append(
                "sha256 is required as a 64-char hex digest of the file's bytes "
                "— without it two runs of this document can read two different "
                f"files and report the same identity (got {digest!r})"
            )
        expect = params.get("expect")
        if (
            expect is not None
            and not is_node_ref(expect)
            and (not isinstance(expect, int) or isinstance(expect, bool) or expect < 0)
        ):
            problems.append(f"expect must be a non-negative integer, got {expect!r}")
        return problems

    def validate_inputs(self, inputs):
        """A source node reads a file; wiring one an input is a mistake."""
        if inputs:
            return [
                f"table-file takes no inputs (got {sorted(inputs)}) — it reads "
                "params['path'], which is what makes it a SOURCE"
            ]
        return []

    def run(self, ctx, inputs):
        path = self.params["path"]
        declared = self.params["sha256"].lower()
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError as exc:
            raise ValueError(
                f"{self.key}: cannot read table file {path!r} (resolved from "
                f"{os.getcwd()!r}): {exc}"
            ) from exc

        actual = hashlib.sha256(raw).hexdigest()
        if actual != declared:
            raise ValueError(
                f"{self.key}: {path!r} has sha256 {actual}, but this document "
                f"declares {declared}. The file on disk is NOT the one this "
                "document was approved against — re-verify the data and update "
                "the digest deliberately, or restore the file. Running anyway "
                "would report this document's identity for a different input"
            )

        try:
            table = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"{self.key}: {path!r} is not valid JSON: {exc}") from exc
        if not isinstance(table, dict):
            raise ValueError(
                f"{self.key}: {path!r} holds a {type(table).__name__}, and a "
                "table is a mapping from key to value — wrap it, or wire a "
                "different kind"
            )

        expect = self.params.get("expect")
        if expect is not None and len(table) != expect:
            raise ValueError(
                f"{self.key}: {path!r} holds {len(table)} entries but this "
                f"document expects {expect}. The count is the provenance check a "
                "reader can make by eye; fix the file or update the document, "
                "but do not let them disagree"
            )

        provenance = {
            "path": path,
            "source": self.params["source"],
            "retrieved": self.params["retrieved"],
            "sha256": actual,
            "entries": len(table),
        }
        self.log.info(
            "table-file %r: %d entr(ies) from %s (retrieved %s, sha256 %s)",
            self.key,
            len(table),
            path,
            provenance["retrieved"],
            actual[:12],
        )
        return {"table": table, "provenance": provenance}


def _non_finite(value, path=""):
    """``(path, spelling)`` of the first NaN / ±Infinity inside ``value``, else ``None``."""
    if isinstance(value, float) and not math.isfinite(value):
        spelling = "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")
        return path, spelling
    if isinstance(value, dict):
        items = ((f"{path}.{k}" if path else str(k), v) for k, v in value.items())
    elif isinstance(value, (list, tuple)):
        items = ((f"{path}[{i}]", v) for i, v in enumerate(value))
    else:
        return None
    for sub_path, item in items:
        found = _non_finite(item, sub_path)
        if found is not None:
            return found
    return None


def _json_text(key, where, value, **spelling):
    """``value`` as JSON text, or a refusal naming ``where`` and the field JSON cannot carry."""
    found = _non_finite(value)
    if found is not None:
        path, name = found
        raise ValueError(
            f"{key}: {where} field {path!r} is {name}, which JSON cannot carry — "
            "a line one reader parses as a number and another refuses is not a "
            "record; drop or replace the value before writing"
        )
    try:
        return json.dumps(value, allow_nan=False, **spelling)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{key}: {where} holds a value JSON has no form for ({exc}) — project "
            "it to JSON scalars, lists and mappings before writing"
        ) from exc


def _atomic_write(path, raw):
    """Land ``raw`` at ``path`` via a same-directory temp file, fsync and replace."""
    # An interrupted write leaves the old file or the new one, never a
    # half-file that still parses.
    tmp = f"{path}.tmp-{os.getpid()}"
    try:
        with open(tmp, "wb") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class FileWrite(Node):
    """The write discipline every file-writing kind shares (role ``report``).

    A node computes something a later run must pin — a frozen vocabulary,
    a resolved universe, the rows a fold scored — and a member of this
    family persists it and proves what it wrote. Everything that makes a
    writer safe to point at a store other processes share lives here
    ONCE: the four knobs, the ``expect`` cross-check before any byte
    lands, the refusal to clobber, the refusal to create a tree, the
    ``~`` expansion, the atomic temp-fsync-replace and the provenance
    block. A member supplies :meth:`render` and :meth:`payload_problems`,
    names its port and its unit, and may add to the outputs through
    :meth:`emit`.

    Parameters
    ----------
    params : dict
        ``path`` (str, REQUIRED) — where to write; the parent directory
        must already exist, because an implicitly created tree is how a
        mistyped path becomes a second store. ``source`` (str, REQUIRED)
        — what this file IS, the field a reader checks first. ``overwrite``
        (bool, default ``False``) — an existing ``path`` is an ERROR
        unless declared; there is no third setting, "write it if it
        changed" is a document's decision. ``expect`` (non-negative int,
        optional) — the entry count, cross-checked before anything is
        written.

    Attributes
    ----------
    _PORT : str
        The input port carrying the payload.
    _UNIT : str
        What the count is called in provenance (``entries``, ``rows``).
    _WHAT : str
        How a refusal names the payload (``this table``).

    Examples
    --------
    A member that writes one text line per string::

        class LinesWrite(FileWrite):
            _PORT, _UNIT, _WHAT = "lines", "lines", "these lines"

            def payload_problems(self, lines):
                return [] if isinstance(lines, list) else ["lines must be a list"]

            def render(self, lines):
                raw = "".join(f"{line}\\n" for line in lines).encode("utf-8")
                return raw, len(lines)

        node = LinesWrite("log", {"path": "out/run.log", "source": "the run's log lines"})
        out = node.run(ctx, {"lines": ["planned", "ran"]})
        out["provenance"]["lines"]   # 2
    """

    role = "report"
    outputs = ("path", "provenance")

    #: The member's port, its unit in provenance, and how a refusal names
    #: the payload — the three things that differ between a table and a stream.
    _PORT = ""
    _UNIT = ""
    _WHAT = ""

    _PARAMS = ("path", "source", "overwrite", "expect")

    @classmethod
    def validate_params(cls, params):
        """Problems with this node's declared knobs, empty when none.

        Parameters
        ----------
        params : dict
            The node's ``params`` block, possibly carrying unmaterialized
            ``$``-references.

        Returns
        -------
        list of str
            One message per problem; empty when the params are legal.
        """
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        path = params.get("path")
        if path is None:
            problems.append(f"path is required — name the file {cls._WHAT} is written to")
        elif not is_node_ref(path) and (not isinstance(path, str) or not path):
            problems.append(f"path must be a non-empty string, got {path!r}")
        source = params.get("source")
        if not is_node_ref(source) and (
            not isinstance(source, str) or not source.strip()
        ):
            problems.append(
                "source is required — a file written with no stated origin "
                "cannot be pinned by a reader later, which is the whole "
                f"point of writing it here (got {source!r})"
            )
        overwrite = params.get("overwrite", False)
        if not is_node_ref(overwrite) and not isinstance(overwrite, bool):
            problems.append(f"overwrite must be a bool, got {overwrite!r}")
        expect = params.get("expect")
        if (
            expect is not None
            and not is_node_ref(expect)
            and (not isinstance(expect, int) or isinstance(expect, bool) or expect < 0)
        ):
            problems.append(f"expect must be a non-negative integer, got {expect!r}")
        return problems

    def validate_inputs(self, inputs):
        """Problems with the materialized inputs, empty when none.

        Parameters
        ----------
        inputs : dict
            The wired ports; only the member's port is read, through
            :meth:`payload_problems`.

        Returns
        -------
        list of str
            One message per problem; empty when the payload is writable.
        """
        return list(self.payload_problems((inputs or {}).get(self._PORT)))

    @abstractmethod
    def payload_problems(self, payload):
        """Problems with what arrived on the member's port, empty when none.

        Container shape only — never a walk: a one-shot iterable consumed
        here would reach :meth:`run` exhausted.

        Parameters
        ----------
        payload : object
            Whatever the port carries; ``None`` when it is unwired.

        Returns
        -------
        list of str
            One message per problem.
        """

    @abstractmethod
    def render(self, payload):
        """The payload as the exact bytes to write, plus its entry count.

        Parameters
        ----------
        payload : object
            The validated payload.

        Returns
        -------
        tuple
            ``(raw, count)`` — the ``bytes`` and the ``int`` that
            ``expect`` is cross-checked against.

        Raises
        ------
        ValueError
            Naming the entry JSON cannot carry, before anything reaches disk.
        """

    def emit(self, path, provenance):
        """This node's outputs — override to ADD to them, never to drop one.

        Parameters
        ----------
        path : str
            The file written, ``~`` expanded.
        provenance : dict
            The block :meth:`run` built.

        Returns
        -------
        dict
            ``{"path", "provenance"}`` by default.
        """
        return {"path": path, "provenance": provenance}

    def _refuse_wrong_count(self, path, count):
        """``expect`` disagreeing with the rendered count refuses before any byte lands."""
        expect = self.params.get("expect")
        if expect is not None and count != expect:
            raise ValueError(
                f"{self.key}: refusing to write {path!r} — {self._WHAT} holds "
                f"{count} {self._UNIT} and this document expects {expect}. "
                "Output that came out the wrong shape must not reach disk"
            )

    def _refuse_clobber(self, path):
        """An existing target refuses unless the document declares ``overwrite``."""
        if os.path.exists(path) and not self.params.get("overwrite", False):
            raise FileExistsError(
                f"{self.key}: {path!r} already exists and this document does "
                "not declare overwrite. Refusing to replace it — a silent "
                "overwrite of an authoritative file is how data disappears "
                "(I-233). Set params.overwrite=true to replace it "
                "deliberately, or write to a new path"
            )

    def _refuse_missing_parent(self, path):
        """A missing parent directory refuses — it is never created implicitly."""
        parent = os.path.dirname(os.path.abspath(path))
        if not os.path.isdir(parent):
            raise NotADirectoryError(
                f"{self.key}: the parent directory {parent!r} of {path!r} does "
                "not exist. Refusing to create it — an implicitly-created tree "
                "is how a mistyped path becomes a second store nobody knows about"
            )

    def _provenance(self, ctx, path, raw, count):
        """The pinnable block: path, source, retrieved, sha256, the unit's count, bytes."""
        return {
            "path": path,
            "source": self.params["source"],
            "retrieved": str(getattr(ctx, "asof", "") or "")[:10],
            "sha256": hashlib.sha256(raw).hexdigest(),
            self._UNIT: count,
            "bytes": len(raw),
        }

    def run(self, ctx, inputs):
        """Render, cross-check, refuse what must be refused, write atomically, prove it.

        ``~`` is expanded, and the EXPANDED path is what provenance
        reports: none of the readers in this repo expand it (an adapter's
        own data-root helper is where that expansion lives, when it
        exists), so emitting the tilde form would hand ``table-file`` a
        path it would look for under a directory literally named ``~``.

        Parameters
        ----------
        ctx : NodeContext
            The run frame; ``asof`` dates the provenance.
        inputs : dict
            The wired ports; the member's port carries the payload.

        Returns
        -------
        dict
            :meth:`emit`'s outputs — ``path`` and ``provenance`` at least.

        Raises
        ------
        ValueError
            A count ``expect`` disagrees with, or a value JSON cannot carry.
        FileExistsError
            The target exists and ``overwrite`` is not declared.
        NotADirectoryError
            The parent directory does not exist.
        """
        path = os.path.expanduser(self.params["path"])
        raw, count = self.render(inputs[self._PORT])
        self._refuse_wrong_count(path, count)
        self._refuse_clobber(path)
        self._refuse_missing_parent(path)
        _atomic_write(path, raw)
        provenance = self._provenance(ctx, path, raw, count)
        self.log.info(
            "%s %r: %d %s, %d byte(s) -> %s (sha256 %s)",
            type(self).__name__,
            self.key,
            count,
            self._UNIT,
            len(raw),
            path,
            provenance["sha256"][:12],
        )
        return self.emit(path, provenance)


class TableWrite(FileWrite):
    """Materialise one keyed table to a declared path (role ``report``).

    The write counterpart of :class:`TableFile`, and the mapping member of
    :class:`FileWrite`. A node computes something a later run must pin — a
    frozen vocabulary, a resolved universe, a settlement map — and this
    persists it with the provenance that makes it pinnable, then proves
    what it wrote.

    Parameters
    ----------
    params : dict
        :class:`FileWrite`'s four knobs; ``expect`` counts top-level
        entries.

    Inputs
    ------
    ``table`` (REQUIRED)
        The mapping to persist — wire it from whatever produced it
        (``"$panels.vocab"``). Keys are stringified for JSON, and a
        non-mapping is refused by name.

    Outputs
    -------
    ``path``
        The file written, so a downstream node can name it.
    ``provenance``
        ``{path, source, retrieved, sha256, entries, bytes}`` — a superset
        of the params :class:`TableFile` requires to read it back.

    Examples
    --------
    Persist a frozen vocabulary for the next document to pin::

        node = TableWrite(
            "vocab_file",
            {"path": "out/vocab.json", "source": "the panels node's frozen vocabulary"},
        )
        out = node.run(ctx, {"table": vocab})
        out["provenance"]["sha256"]   # what table-file will demand
    """

    _PORT, _UNIT, _WHAT = "table", "entries", "this table"

    def payload_problems(self, table):
        """A mapping, or one problem naming what arrived instead.

        Parameters
        ----------
        table : object
            What the ``table`` port carries.

        Returns
        -------
        list of str
            Empty for a mapping.
        """
        if not isinstance(table, dict):
            return [
                "table must be wired from a node output holding a mapping "
                f"(e.g. '$panels.vocab'), got {type(table).__name__}"
            ]
        return []

    def render(self, table):
        """The table as indented JSON with sorted keys, plus its entry count.

        Sorted, so the digest is a function of the TABLE and not of dict
        ordering — two runs that computed the same table must produce the
        same digest or the pin is worthless.

        Parameters
        ----------
        table : dict
            The mapping; keys are stringified for JSON.

        Returns
        -------
        tuple
            ``(raw, entries)``.
        """
        table = {str(k): v for k, v in table.items()}
        text = _json_text(self.key, "the table", table, **_TABLE_SPELLING)
        return (text + "\n").encode("utf-8"), len(table)


class RecordsWrite(FileWrite):
    """Materialise a record stream as newline-JSON (role ``report``).

    The stream member of :class:`FileWrite` and :class:`TableWrite`'s
    sibling (ADR-0085): one JSON object per line in the canonical
    spelling — sorted keys, compact, ASCII, a trailing newline — so two
    runs that scored the same rows write byte-identical files and the
    digest in ``metrics`` is a pin, not a timestamp. Rows must be mappings
    JSON can carry: a frozen envelope, a NaN or ±Infinity, or a value JSON
    has no form for is refused BY NAME (row index and dotted field path),
    and nothing reaches disk.

    Parameters
    ----------
    params : dict
        :class:`FileWrite`'s four knobs; ``expect`` counts rows.

    Inputs
    ------
    ``records`` (REQUIRED)
        A list or tuple of mapping rows. A one-shot iterable is refused by
        name — validation would consume it and ``run`` would write nothing.

    Outputs
    -------
    ``path``
        The file written.
    ``provenance``
        ``{path, source, retrieved, sha256, rows, bytes}``.
    ``metrics``
        ``{rows, bytes, sha256}`` — the digest reaches the run record and
        the sinks beside the counts.

    Examples
    --------
    Persist the rows a fold scored, refusing a second write::

        node = RecordsWrite(
            "scored_rows",
            {"path": "out/scored.jsonl", "source": "rows scored on val", "expect": 1200},
        )
        out = node.run(ctx, {"records": rows})
        out["metrics"]["sha256"]   # the digest of the exact bytes written
    """

    outputs = ("path", "provenance", "metrics")

    _PORT, _UNIT, _WHAT = "records", "rows", "this record stream"

    def payload_problems(self, records):
        """A list or tuple of rows, or one problem naming what arrived instead.

        Parameters
        ----------
        records : object
            What the ``records`` port carries.

        Returns
        -------
        list of str
            Empty for a list or tuple; the row-level checks wait for ``run``.
        """
        if isinstance(records, (str, bytes, dict)) or not isinstance(
            records, (list, tuple)
        ):
            return [
                "records must be a list or tuple of mapping rows (a one-shot "
                "iterable would be consumed by validation and reach run() "
                f"empty), got {records!r}"
            ]
        return []

    def render(self, records):
        """The rows as canonical newline-JSON, plus the row count.

        Parameters
        ----------
        records : list or tuple
            Mapping rows; keys are stringified for JSON.

        Returns
        -------
        tuple
            ``(raw, rows)``.

        Raises
        ------
        ValueError
            Naming a row that is not a mapping, keys that collide after
            stringification, or a field JSON cannot carry.
        """
        lines = []
        for index, row in enumerate(records):
            if not isinstance(row, dict):
                raise ValueError(
                    f"{self.key}: row {index} is a {type(row).__name__}, not a "
                    "mapping — JSON has no form for a frozen envelope; project "
                    "the rows to mappings before writing"
                )
            normalized = {}
            original = {}
            for field, value in row.items():
                name = str(field)
                if name in normalized:
                    raise ValueError(
                        f"{self.key}: row {index} keys {original[name]!r} and "
                        f"{field!r} both stringify to {name!r} - writing either "
                        "would silently discard the other"
                    )
                normalized[name] = value
                original[name] = field
            lines.append(
                _json_text(self.key, f"row {index}", normalized, **_LINE_SPELLING)
            )
        raw = "".join(f"{line}\n" for line in lines).encode("utf-8")
        return raw, len(lines)

    def emit(self, path, provenance):
        """The base outputs plus ``metrics``.

        Parameters
        ----------
        path : str
            The file written.
        provenance : dict
            The block :meth:`FileWrite.run` built.

        Returns
        -------
        dict
            ``{"path", "provenance", "metrics"}``.
        """
        metrics = {k: provenance[k] for k in (self._UNIT, "bytes", "sha256")}
        return {"path": path, "provenance": provenance, "metrics": metrics}


#: The kinds this module ships, in registration order.
_KINDS = (
    ("table-file", TableFile),
    ("table-write", TableWrite),
    ("records-write", RecordsWrite),
)


def register(registry=None):
    """Register ``table-file``, ``table-write`` and ``records-write`` into
    ``registry``, ``owned=False``.

    Idempotent by SKIPPING a name already present, matching
    :func:`dskit.pipeline.kinds_flow.register`.
    """
    registry = DEFAULT_NODE_KINDS if registry is None else registry
    for name, cls in _KINDS:
        if name not in registry:
            registry.register(name, cls, owned=False)
    return registry
