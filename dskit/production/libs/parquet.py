"""``libs/parquet.py`` — the ``run`` reference over a run's predictions (§5.10.2).

The ``Reference`` members ``monitors.py`` ships compare a live window
against something the operator produced: the first values the stream
offered, its recent past, or a profile someone saved. This pack adds the
one comparison population nobody has to produce — the run's OWN scored
predictions — so a drift monitor asks the only question that matters
about a model in production: does what it is being asked to score still
look like what it was validated on?

The population is read from ``<run_dir>/artifacts/<node>/predictions.parquet``,
the ``dskit.pipeline.predictions`` layout, whose ``PREDICTIONS_FILE`` and
``find_predictions`` are imported rather than spelled again here — the
layout has one owner, and a serving path that restated it would drift the
day a run directory changed shape. Every node that saved rows contributes
them, in node order, because the reference population is "the run's scored
predictions" and the params carry no node selector to choose between two.

Three rules are refusals, and they are the point of the module:

* **Nothing is read at construction.** A document is validated on machines
  that hold no run directory; a reference that read when it was built
  would make ``validate`` need the artifact. The read happens once, lazily,
  on the first ``sample()`` — ``Snapshot``'s precedent, which reads at
  ``fit`` for the same reason.
* **``add()`` refuses.** A run reference is a fixed anchor by definition.
  Quietly absorbing the values that leave a monitor's window would make it
  drift with the very thing it is there to measure drift against.
* **``fingerprint()`` is the file's digest**, so ``plan`` binds the
  predictions into the release like any other artifact. That is what stops
  a re-scored run from moving a live alarm threshold under a release hash
  that never changed.

``pyarrow`` is named only inside the read, per §8's tier-2 rule: importing
this pack must not import parquet, and a serve process that declares no
``run`` reference never loads it at all.
"""

from pathlib import Path

from dskit.onboarding.base import file_digest
from dskit.pipeline.node import check_int_param
from dskit.pipeline.predictions import PREDICTIONS_FILE, find_predictions
from dskit.production.base import ProductionError, _check_str, canonical_hash
from dskit.production.monitors import REFERENCE_KINDS, Reference

__all__ = [
    "DEFAULT_REFERENCE_MAX_ROWS",
    "RunReference",
]

#: How many of a run's rows the reference keeps when the document names no
#: ``max_rows``. The cap is not about memory alone: the population rides in
#: the §6 ``snapshot`` record through ``Reference.state()``, so an uncapped
#: reference over a long walk-forward would write itself into the ledger
#: on every checkpoint.
DEFAULT_REFERENCE_MAX_ROWS = 10_000


class RunReference(Reference):
    """The run's own scored predictions as a monitor's comparison population.

    Registered as ``run`` in ``REFERENCE_KINDS`` (§4.3: import is
    registration). The file is read once, lazily, on the first ``sample()``;
    a missing file, a missing column or a column that is not numeric refuses
    THERE rather than at construction, and ``add()`` refuses always.

    When the run holds more rows than ``max_rows``, the sample is spread
    evenly across the file rather than taken from its head: predictions are
    written a ``(series, horizon)`` block at a time, so the head of a file
    is one series and would make the anchor stand for one instrument.

    Parameters
    ----------
    params : dict
        ``run_dir`` (str, required): the run directory whose predictions are
        the population; ``column`` (str, default the owning monitor's
        ``field``): which prediction column to read; ``max_rows`` (int >= 1,
        default ``DEFAULT_REFERENCE_MAX_ROWS``): the largest population to
        keep. ``notes`` is allowed, as everywhere.
    field : str or None, keyword-only
        The owning monitor's field, supplied by the monitor; it is the
        default ``column``, and a reference with neither refuses.

    Examples
    --------
    The anchor a drift monitor over ``yhat`` would build for a finished
    run, which reads nothing until it is asked for the population::

        reference = RunReference({"run_dir": "runs/9f2c"}, field="yhat")
        len(reference.sample())  # the run's rows, capped at max_rows
        reference.fingerprint()  # the digest `plan` binds into the release
    """

    _PARAMS = ("run_dir", "column", "max_rows")

    @classmethod
    def _check(cls, problems, params):
        """Require a run directory; the column may come from the monitor's field."""
        _check_str(problems, "run_dir", params.get("run_dir"))
        if "column" in params:
            _check_str(problems, "column", params["column"])
        check_int_param(
            problems, "max_rows", params.get("max_rows", DEFAULT_REFERENCE_MAX_ROWS), ge=1
        )

    def _configure(self, params):
        """Take the run directory, the column and the cap; touch no file."""
        self._run_dir = params["run_dir"]
        self._column = params.get("column", self._field)
        self._max_rows = int(params.get("max_rows", DEFAULT_REFERENCE_MAX_ROWS))
        self._loaded = False
        if self._column is None:
            raise ProductionError(
                [
                    "run reference needs a column: name one in params, or let the "
                    "owning monitor's field supply it"
                ]
            )

    def add(self, value):
        """Refuse: a run reference is a fixed anchor and never grows.

        Parameters
        ----------
        value : number
            The observation leaving the monitor's window; never taken.

        Returns
        -------
        None
            Nothing is ever added.

        Raises
        ------
        ProductionError
            Always.
        """
        raise ProductionError(
            [
                f"run reference: {self._run_dir} is a fixed anchor — add() is refused, "
                "because a reference that absorbed the values leaving a monitor's "
                "window would drift with the thing it measures drift against"
            ]
        )

    def fit(self, values):
        """Ignore the training values: the run's rows are the population.

        Parameters
        ----------
        values : iterable of number
            Unused. ``Monitor.fit`` offers every training value to every
            reference, so this hook must accept them; the run's own rows are
            what this reference stands for.

        Returns
        -------
        None
            Nothing changes, and nothing is read — ``sample()`` reads.
        """

    def sample(self):
        """Return the run's scored predictions, reading them on the first call.

        Returns
        -------
        tuple of number
            The column's values, capped at ``max_rows`` and evenly spread
            across the file when the cap bites.

        Raises
        ------
        ProductionError
            If the run saved no predictions, or the column is missing or not
            numeric. The read happens here, never at construction.
        """
        if not self._loaded:
            self._values = self._capped(self._read())
            self._loaded = True
        return tuple(self._values)

    def fingerprint(self):
        """Return the digest of the predictions this reference stands on.

        Returns
        -------
        str
            The canonical hash of ``{path relative to the run: sha256}``
            over every predictions file the run holds — what ``plan`` binds
            into the release, so a re-scored run cannot move a live alarm
            threshold under an unchanged release hash.

        Raises
        ------
        ProductionError
            If the run saved no predictions.
        """
        root = Path(self._run_dir)
        return canonical_hash(
            {
                Path(path).relative_to(root).as_posix(): file_digest(path)
                for path in self._paths()
            }
        )

    def restore(self, state):
        """Take the population back from a ``state()`` payload and read no file.

        Parameters
        ----------
        state : dict
            Exactly the ``Reference.state()`` shape.

        Returns
        -------
        None
            The restored population is the one the snapshot folded; a
            restart replays it rather than re-reading the run, which may by
            then have been archived.

        Raises
        ------
        ProductionError
            On an unknown or missing key, or a non-list ``values``.
        """
        super().restore(state)
        self._loaded = True

    def _paths(self):
        """Return the run's predictions files; refuse when it saved none."""
        paths = find_predictions(self._run_dir)
        if not paths:
            raise ProductionError(
                [
                    f"run reference: {self._run_dir} holds no {PREDICTIONS_FILE} — "
                    "the run saved no predictions to compare a live window against"
                ]
            )
        return paths

    def _read(self):
        """Return the column's values across every predictions file the run holds."""
        import pyarrow.parquet as pq
        import pyarrow.types as types

        values = []
        for path in self._paths():
            schema = pq.read_schema(path)
            if self._column not in schema.names:
                raise ProductionError(
                    [
                        f"run reference: {path} has no column {self._column!r} — "
                        f"it holds {list(schema.names)}"
                    ]
                )
            column_type = schema.field(self._column).type
            if not (types.is_integer(column_type) or types.is_floating(column_type)):
                raise ProductionError(
                    [
                        f"run reference: column {self._column!r} of {path} is "
                        f"{column_type}, and a comparison population must be numeric"
                    ]
                )
            column = pq.read_table(path, columns=[self._column]).column(self._column)
            values.extend(value for value in column.to_pylist() if value is not None)
        return values

    def _capped(self, values):
        """Return at most ``max_rows`` values, evenly spread rather than the head."""
        if len(values) <= self._max_rows:
            return values
        return values[:: len(values) // self._max_rows][: self._max_rows]


REFERENCE_KINDS.register("run", RunReference)
