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

Three properties it does not compromise on, because it is the first thing here
whose whole job is writing into a store other processes share:

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
import os
import re

from dskit.pipeline.document import is_node_ref
from dskit.pipeline.kinds_stats import _reject_unknown
from dskit.pipeline.node import DEFAULT_NODE_KINDS, Node

__all__ = ["TableFile", "TableWrite", "register"]

#: A hex sha256, and nothing that merely looks like one.
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

#: ISO date, the form every provenance field in this repo already uses.
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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


class TableWrite(Node):
    """Materialise one keyed table to a declared path (role ``report``).

    The write counterpart of :class:`TableFile`. A node computes something
    a later run must pin — a frozen vocabulary, a resolved universe, a
    settlement map — and this persists it with the provenance that makes it
    pinnable, then proves what it wrote.

    Params
    ------
    ``path`` (REQUIRED)
        Where to write. The parent directory must already exist: creating a
        tree implicitly is how a mistyped path silently becomes a new store.
    ``source`` (REQUIRED)
        What this table IS — the field a reader of the produced file checks
        first, and the one :class:`TableFile` will demand when it is pinned.
    ``overwrite``
        Default ``false``: an existing ``path`` is an ERROR. Set ``true`` to
        replace deliberately. There is no third setting — "write it if it
        changed" is a decision a document makes, not a file writer.
    ``expect``
        Expected number of top-level entries, cross-checked before anything
        is written. A table that came out the wrong shape must not reach
        disk at all.

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
    """

    role = "report"
    outputs = ("path", "provenance")

    _PARAMS = ("path", "source", "overwrite", "expect")

    @classmethod
    def validate_params(cls, params):
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        path = params.get("path")
        if path is None:
            problems.append("path is required — name the file this table is written to")
        elif not is_node_ref(path) and (not isinstance(path, str) or not path):
            problems.append(f"path must be a non-empty string, got {path!r}")
        source = params.get("source")
        if not is_node_ref(source) and (
            not isinstance(source, str) or not source.strip()
        ):
            problems.append(
                "source is required — a file written with no stated origin "
                "cannot be pinned by table-file later, which is the whole "
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
        table = (inputs or {}).get("table")
        if not isinstance(table, dict):
            return [
                "table must be wired from a node output holding a mapping "
                f"(e.g. '$panels.vocab'), got {type(table).__name__}"
            ]
        return []

    def run(self, ctx, inputs):
        # ``~`` is expanded, and the EXPANDED path is what provenance
        # reports: none of the readers in this repo expand it (an
        # adapter's own data-root helper is where that expansion lives,
        # when it exists), so emitting the tilde form would hand ``table-file``
        # a path it would look for under a directory literally named ``~``.
        path = os.path.expanduser(self.params["path"])
        table = {str(k): v for k, v in inputs["table"].items()}

        expect = self.params.get("expect")
        if expect is not None and len(table) != expect:
            raise ValueError(
                f"{self.key}: refusing to write {path!r} — the table holds "
                f"{len(table)} entries and this document expects {expect}. A "
                "table that came out the wrong shape must not reach disk"
            )

        if os.path.exists(path) and not self.params.get("overwrite", False):
            raise FileExistsError(
                f"{self.key}: {path!r} already exists and this document does "
                "not declare overwrite. Refusing to replace it — a silent "
                "overwrite of an authoritative file is how data disappears "
                "(I-233). Set params.overwrite=true to replace it "
                "deliberately, or write to a new path"
            )
        parent = os.path.dirname(os.path.abspath(path))
        if not os.path.isdir(parent):
            raise NotADirectoryError(
                f"{self.key}: the parent directory {parent!r} of {path!r} does "
                "not exist. Refusing to create it — an implicitly-created tree "
                "is how a mistyped path becomes a second store nobody knows about"
            )

        # Canonical bytes: sorted keys, so the digest is a function of the
        # TABLE and not of dict ordering — two runs that computed the same
        # table must produce the same digest or the pin is worthless.
        raw = (
            json.dumps(table, indent=2, sort_keys=True, allow_nan=False) + "\n"
        ).encode("utf-8")

        # Atomic: an interrupted write leaves the old file or the new one,
        # never a half-file that still parses.
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

        provenance = {
            "path": path,
            "source": self.params["source"],
            "retrieved": str(getattr(ctx, "asof", "") or "")[:10],
            "sha256": hashlib.sha256(raw).hexdigest(),
            "entries": len(table),
            "bytes": len(raw),
        }
        self.log.info(
            "table-write %r: %d entr(ies), %d byte(s) -> %s (sha256 %s)",
            self.key,
            provenance["entries"],
            provenance["bytes"],
            path,
            provenance["sha256"][:12],
        )
        return {"path": path, "provenance": provenance}


def register(registry=None):
    """Register ``table-file`` and ``table-write`` into ``registry``,
    ``owned=False``.

    Idempotent by SKIPPING a name already present, matching
    :func:`dskit.pipeline.kinds_flow.register`.
    """
    registry = DEFAULT_NODE_KINDS if registry is None else registry
    for name, cls in (("table-file", TableFile), ("table-write", TableWrite)):
        if name not in registry:
            registry.register(name, cls, owned=False)
    return registry
