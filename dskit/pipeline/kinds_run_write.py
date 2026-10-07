"""``records-write-run`` — a record-stream writer whose file, and so whose published stream, is named by the run (ADR-0247).

Why. A table that a later run reads is published back through onboarding, and the
``localtables`` connector is INCREMENTAL: a pull emits the rows strictly after the stream's cursor.
Rewrite a changed table under the SAME file name and re-acquire, and the pull exits 0 with no
snapshot, so a read serves the OLD rows; append instead and two configurations share one stream.
Nothing in the platform stops a project doing either, and the failure is silent.

What. :class:`RecordsWriteRun` is a sibling of ``records-write`` (it subclasses the public
:class:`~dskit.pipeline.kinds_table.RecordsWrite` and changes none of its guarantees: canonical
newline-JSON, no clobber, no created directories, atomic write, ``expect``, the digest in
``metrics``). Its ``path`` carries exactly ONE ``{run}``, in the file stem, and the writer expands
it to the name of the run directory (``<document>-<as-of>-<identity hash>``; the hash already
covers the configuration and the data it read) and stamps that name on every row as ``run_id``.
With ``localtables`` ``layout: file`` the file stem is the stream name, so each run is its own
stream with its own cursor: two configurations can neither merge nor shadow, the older table stays
readable, and a consumer that names ``<prefix>-<run>`` records WHICH table it read.

What is refused, and when. At validation (the planner calls it for every node, before any node
runs): a ``path`` without exactly one ``{run}``, with it outside the file stem, or whose stem could
not be a stream name once expanded. At run: a run name that cannot be a stream name (lowercase
letters, digits, ``_`` and ``-``), before any byte is written, and a row that already carries a
different ``run_id``. A node is handed no document name until it runs, so the one part of the run
name a node cannot see at plan time is the document's own name; :func:`run_name_problems` is the
rule, and a caller that holds the document (a config test, a wrapper's plan step) can apply it to
``document.name`` to refuse before any node computes. The rest of a run name (``-<as-of>-<hash>``)
is always stream-safe.

The kind is named ``records-write-run`` in :data:`NODE_KINDS` and is claimed only by the opt-in
:func:`register`, or reached by import path
(``dskit.pipeline.kinds_run_write:RecordsWriteRun``); importing this module registers nothing.
``records-write`` keeps its literal ``path`` and stays right for tables that are never published.

Import cost: stdlib + dskit.pipeline.
"""

import copy
import os
import re

from dskit.pipeline.document import is_node_ref
from dskit.pipeline.kinds_table import RecordsWrite
from dskit.pipeline.node import DEFAULT_NODE_KINDS

__all__ = ["NODE_KINDS", "RUN_ID_FIELD", "RUN_PLACEHOLDER", "RecordsWriteRun", "register", "run_name_problems"]

#: The field every row is stamped with: the name of the run that wrote it.
RUN_ID_FIELD = "run_id"
#: The one token a ``path`` expands, to the run directory's name.
RUN_PLACEHOLDER = "{run}"

#: What a stream name may be: the platform's segment rule (lowercase letters, digits, ``_`` and
#: ``-``, the first a letter or digit). It is private to onboarding, so it is restated here and the
#: agreement is pinned by a test against ``payload_files``' own refusal of a bad stream name.
_STREAM_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*")
#: A name that satisfies the rule, standing in for the run name while a path is checked at plan time.
_SAMPLE_RUN = "run"


def run_name_problems(name):
    """List why ``name`` cannot be (part of) a stream name, empty when it can: the one rule.

    Applies to a run directory's name and, because the rest of that name
    (``-<as-of>-<hash>``) is always safe, to a document's name.

    Parameters
    ----------
    name : str
        A run directory's name, or a document's name.

    Returns
    -------
    list of str
        One problem naming ``name``, or an empty list.

    Examples
    --------
    Refuse a document name that would make an unusable stream, before anything runs::

        run_name_problems("features-15m")    # -> []
        run_name_problems("features.v2")     # -> ["'features.v2' cannot be a stream name ..."]
    """
    if isinstance(name, str) and _STREAM_NAME.fullmatch(name):
        return []
    return [f"{name!r} cannot be a stream name (lowercase letters, digits, _ and -, "
            "starting with a letter or digit)"]


class RecordsWriteRun(RecordsWrite):
    """Write a record stream as newline-JSON at a path named by the run, each row stamped ``run_id``.

    Role ``report``; input ``records``; outputs ``path`` (the expanded one), ``provenance`` and
    ``metrics``, exactly as :class:`~dskit.pipeline.kinds_table.RecordsWrite`.

    Parameters
    ----------
    params : dict
        :class:`~dskit.pipeline.kinds_table.RecordsWrite`'s knobs and no others. ``path`` must carry
        exactly one ``{run}``, in the file stem (the part of the file name before its extension),
        and the stem must be a stream name once ``{run}`` is expanded.

    Examples
    --------
    One table file, hence one stream, per run::

        node = RecordsWriteRun("write", {"path": "~/features/table-{run}.jsonl",
                                         "source": "feature rows"})
        out = node.run(ctx, {"records": rows})
        # -> out["path"] ends with "table-<document>-<as-of>-<hash8>.jsonl", each row has run_id
    """

    @classmethod
    def validate_params(cls, params):
        """List problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            The writer's problems, plus the ``path`` problems of :meth:`path_problems`.
        """
        problems = super().validate_params(params)
        path = params.get("path")
        if isinstance(path, str) and path and not is_node_ref(path):
            problems += cls.path_problems(path)
        return problems

    @classmethod
    def path_problems(cls, path):
        """List why ``path`` cannot make one stream per run, empty when it can.

        Parameters
        ----------
        path : str
            A literal path template.

        Returns
        -------
        list of str
            One problem, or an empty list.
        """
        if path.count(RUN_PLACEHOLDER) != 1:
            return [f"path must carry exactly one {RUN_PLACEHOLDER}: a fixed name would let a changed table "
                    f"shadow the previous one under the same stream, got {path!r}"]
        name = os.path.basename(path)
        if RUN_PLACEHOLDER not in name:
            return [f"{RUN_PLACEHOLDER} must be in the file name, not a directory: a stream is named by its "
                    f"file, so a directory would not separate the runs, got {path!r}"]
        stem = os.path.splitext(name)[0]
        if RUN_PLACEHOLDER not in stem:
            return [f"{RUN_PLACEHOLDER} must be in the file stem, before the extension, got {path!r}"]
        if run_name_problems(stem.replace(RUN_PLACEHOLDER, _SAMPLE_RUN)):
            return [f"the file stem {stem!r} must be a stream name once {RUN_PLACEHOLDER} is expanded "
                    f"(lowercase letters, digits, _ and -, starting with a letter or digit), got {path!r}"]
        return []

    @classmethod
    def run_name(cls, ctx):
        """Return the run directory's name, refusing one that cannot be a stream name.

        Parameters
        ----------
        ctx : NodeContext or None
            The run frame.

        Returns
        -------
        str
            The base name of ``ctx.run_dir``.

        Raises
        ------
        ValueError
            When there is no run frame, or the name fails :func:`run_name_problems`.
        """
        run_dir = getattr(ctx, "run_dir", "") if ctx is not None else ""
        if not run_dir:
            raise ValueError(f"{cls.__name__} names its file by the run: it needs the run frame (ctx.run_dir)")
        name = os.path.basename(os.path.normpath(os.fspath(run_dir)))
        problems = run_name_problems(name)
        if problems:
            raise ValueError(f"the run name {name!r} cannot name a stream: {problems[0]}; "
                             "name the document in lowercase letters, digits, _ and -")
        return name

    def _stamped(self, records, run):
        """Return new rows carrying ``run_id``; a row naming another run is refused, non-mappings pass on."""
        out = []
        for index, row in enumerate(records):
            if isinstance(row, dict):
                held = row.get(RUN_ID_FIELD, run)
                if held != run:
                    raise ValueError(f"{self.key}: row {index} already carries {RUN_ID_FIELD}={held!r}, not this "
                                     f"run {run!r}; stamping would overwrite the run that made it")
                row = {**row, RUN_ID_FIELD: run}
            out.append(row)
        return out

    def run(self, ctx, inputs):
        """Stamp the rows, expand ``{run}`` and write, leaving this node's params untouched.

        Parameters
        ----------
        ctx : NodeContext
            The run frame; its run directory's name replaces ``{run}``.
        inputs : dict
            ``records``: mapping rows.

        Returns
        -------
        dict
            :class:`~dskit.pipeline.kinds_table.RecordsWrite`'s outputs, ``path`` being the expanded one.

        Raises
        ------
        ValueError
            No run frame, an unusable run name, or a row stamped with another run: nothing is written.
        """
        name = self.run_name(ctx)
        stamped = self._stamped(inputs["records"], name)
        expanded = copy.copy(self)
        expanded.params = {**self.params, "path": self.params["path"].replace(RUN_PLACEHOLDER, name)}
        return super(RecordsWriteRun, expanded).run(ctx, {**inputs, "records": stamped})


#: The module's kinds.
NODE_KINDS = (("records-write-run", RecordsWriteRun),)


def register(registry=None):
    """Claim the module's kind names in ``registry`` (default the toolkit's).

    Nothing at toolkit import calls this: a project opts in, or names the kind by import path.

    Parameters
    ----------
    registry : NodeKindRegistry or None
        Where to register; ``None`` means the toolkit default. Idempotent, and a name already
        claimed is left alone.

    Returns
    -------
    None
        Registration is the effect.
    """
    registry = DEFAULT_NODE_KINDS if registry is None else registry
    for name, cls in NODE_KINDS:
        if name not in registry:
            registry.register(name, cls)
