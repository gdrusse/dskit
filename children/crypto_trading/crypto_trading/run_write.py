"""A table writer whose file, and so whose published stream, is named by the run that made it.

INTERIM HOME (PROPOSED ADR-0244): a ``{run}`` path template in dskit's file writers would make this
class unnecessary; it moves there when the ADR lands.

Why. A table that a later run reads is published back through onboarding (``localtables``), and
``localtables`` is INCREMENTAL: a pull emits rows strictly after the stream's cursor (the newest
``decision_ms`` already taken). Re-acquiring a table that was rewritten after a configuration change
therefore exits 0 with ``"snapshot": null`` and read-back still returns the OLD rows (observed:
every row of a changed table mismatched), and rows appended later would mix two configurations in one
table. One file per run, one stream per file, one cursor per stream: tables from two runs can never merge
or shadow each other, and the older table stays retrievable under its own name.

``{run}`` in ``path`` is replaced by the name of the run directory, ``<document>-<as-of>-<identity
hash>`` (the driver names it so; the hash covers the configuration and the data it read), and every
row also carries it as ``run_id``. The run name must be a lowercase, filesystem-safe segment, because the
file stem becomes a stream name.

Import cost: stdlib + dskit.
"""

import os
import re

from dskit.pipeline.kinds_table import RecordsWrite

from . import fields as f

__all__ = ["RunStampedWrite"]

_PLACEHOLDER = "{run}"
#: What a stream name may be (the platform's segment rule: lowercase letters, digits, ``_`` and ``-``).
_STREAM_SAFE = re.compile(r"[a-z0-9_-]+")


class RunStampedWrite(RecordsWrite):
    """Write a record stream as JSON lines at a path named by the run, each row stamped ``run_id``.

    Parameters
    ----------
    params : dict
        :class:`~dskit.pipeline.kinds_table.RecordsWrite`'s knobs; ``path`` must carry exactly one
        ``{run}``, which becomes the run directory's name.

    Examples
    --------
    One table file per run::

        node = RunStampedWrite("write", {"path": "~/features/table-{run}.jsonl",
                                         "source": "feature rows", "overwrite": True})
        out = node.run(ctx, {"records": rows})
        # -> out["path"] ends with "table-<document>-<as-of>-<hash8>.jsonl"
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
            The writer's problems, plus one when ``path`` does not carry exactly one ``{run}``.
        """
        problems = super().validate_params(params)
        path = params.get("path")
        if isinstance(path, str) and path.count(_PLACEHOLDER) != 1:
            problems.append(
                f"path must carry exactly one {_PLACEHOLDER}: a fixed name would let a changed table "
                f"shadow the previous one under the same stream, got {path!r}")
        return problems

    @staticmethod
    def run_name(ctx):
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
            When there is no run frame or the name is not a lowercase filesystem-safe segment.
        """
        if ctx is None or not getattr(ctx, "run_dir", ""):
            raise ValueError("RunStampedWrite names its file by the run: it needs the run frame")
        name = os.path.basename(os.path.normpath(ctx.run_dir))
        if not _STREAM_SAFE.fullmatch(name):
            raise ValueError(
                f"the run name {name!r} cannot be a stream name (lowercase letters, digits, _ and - only); "
                "name the document in lowercase")
        return name

    def run(self, ctx, inputs):
        """Stamp the rows, expand ``{run}`` and write, then restore the template.

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
        """
        name = self.run_name(ctx)
        template = self.params
        stamped = [{**row, f.RUN_ID: name} if isinstance(row, dict) else row for row in inputs["records"]]
        self.params = {**template, "path": template["path"].replace(_PLACEHOLDER, name)}
        try:
            return super().run(ctx, {**inputs, "records": stamped})
        finally:
            self.params = template
