"""Per-row validation predictions — the evidence a fold summary destroys.

A walk-forward keeps, per fold, a handful of reduced numbers: an MSPE
pair, a t, a row count. Every test that judges a FORECAST rather than a
model needs the rows those numbers were reduced FROM — the pooled
Diebold-Mariano gap (ADR-0067), a calibration slope, a per-timestamp
cross-sectional correlation, a scramble null. A mean cannot be unpacked
back into rows, so a walk that saves only summaries has thrown the
evidence away before anyone asks for it.

This module is the store, and it is deliberately narrow (ADR-0064):
seven columns in compact dtypes, ONE parquet file per fold under the
node's artifact directory, written a BLOCK at a time. A writer holds one
block — the rows a single (series, horizon) pair scored — and nothing
accumulates across a fold, so persistence costs a walk under a megabyte
of peak memory however many folds it runs.

Domain-neutral by construction: a "series" is any string key, a
"horizon" any integer lead, and the benchmark column is whatever
constant forecast the caller scored against. Nothing here knows what is
being predicted.

Tier 1: ``pyarrow`` is named only INSIDE functions, so importing a node
never imports parquet.
"""

from __future__ import annotations

import os

__all__ = [
    "PREDICTIONS_FILE",
    "PREDICTION_COLUMNS",
    "PredictionWriter",
    "find_predictions",
    "read_prediction_series",
    "read_predictions",
]

#: One fold's rows, beside the node's other artifacts. Parquet because
#: the columns are numeric and repetitive: a dictionary-encoded series
#: key and a compressed int64 stamp column cost a fraction of the JSON
#: the same rows would need, and a reader can take one column without
#: parsing the rest.
PREDICTIONS_FILE = "predictions.parquet"

#: The row, in order. ``mu`` is the CONSTANT benchmark forecast the fold
#: was scored against — persisted per row rather than derived later,
#: because it is a property of the fold's TRAINING window and is not
#: recoverable from validation rows.
PREDICTION_COLUMNS = ("ts", "series", "fold", "horizon", "yhat", "y", "mu")

#: Stamped into the file so a reader never guesses the row spacing the
#: overlap correction needs, and so a column change is detectable.
_META_VERSION = "1"


def _require_pyarrow():
    """Import pyarrow, or raise with the extra that supplies it."""
    try:
        import pyarrow  # noqa: F401 — availability check only
        import pyarrow.parquet  # noqa: F401
    except Exception as exc:  # noqa: BLE001 — reported, not swallowed
        raise ImportError(
            f"per-row predictions need pyarrow (pip install dskit[parquet]): {exc}"
        ) from exc


def _schema(series_names, meta):
    """Build the seven-column schema, with ``series`` dictionary-encoded.

    Parameters
    ----------
    series_names : sequence of str
        Every series key the file will hold, in dictionary order — fixed
        up front so every row group shares ONE dictionary (a writer that
        replaced it per block would refuse mid-file).
    meta : mapping
        String key/value pairs stamped into the schema metadata.

    Returns
    -------
    pyarrow.Schema
        The schema, carrying ``meta`` plus the column version.
    """
    import pyarrow as pa

    return pa.schema(
        [
            pa.field("ts", pa.int64()),
            pa.field("series", pa.dictionary(pa.int32(), pa.string())),
            pa.field("fold", pa.int16()),
            pa.field("horizon", pa.int16()),
            pa.field("yhat", pa.float32()),
            pa.field("y", pa.float32()),
            pa.field("mu", pa.float32()),
        ],
        metadata={
            **{str(k): str(v) for k, v in meta.items()},
            "columns_version": _META_VERSION,
            "series_names": ",".join(series_names),
        },
    )


class PredictionWriter:
    """Stream one fold's scored validation rows to a parquet file.

    One instance owns one file. :meth:`append` writes a block — the rows
    of a single ``(series, horizon)`` pair — as its own row group and
    keeps nothing, so peak memory is one block's arrow copy however many
    blocks a fold scores. Use it as a context manager; :meth:`close` is
    idempotent.

    Parameters
    ----------
    path : str
        Directory the file lands in (a node's artifact directory). It is
        created when missing.
    series_names : sequence of str
        Every series key this fold will write, fixed at construction so
        the dictionary encoding is stable across row groups.
    fold : int
        The fold's ordinal within its walk; ``-1`` when the run is not a
        fold (a standalone run scores one window and has no ordinal).
    period_minutes : int
        Row spacing in minutes — what turns a horizon into the overlap
        depth an HAC correction needs. Stamped into the file.
    meta : mapping, optional
        Extra string key/values to stamp (the fold cutoff, the run name).
    compression : str
        Parquet codec; ``zstd`` by default.

    Raises
    ------
    ImportError
        When pyarrow is not installed.
    ValueError
        On an empty ``series_names``, or an :meth:`append` whose columns
        disagree in length or whose series was never declared.

    Examples
    --------
    One block, then the file is complete::

        with PredictionWriter(d, ["AAPL"], fold=0, period_minutes=5) as w:
            w.append("AAPL", 5, [1000, 2000], [0.1, -0.2], [0.3, 0.0], 0.01)
    """

    def __init__(
        self, path, series_names, fold=-1, period_minutes=1, meta=None,
        compression="zstd",
    ):
        _require_pyarrow()
        import pyarrow.parquet as pq

        names = [str(s) for s in series_names]
        if not names:
            raise ValueError("series_names must name at least one series")
        seen = {}
        for name in names:
            seen.setdefault(name, len(seen))
        self._index = seen
        self._names = list(seen)
        self._fold = int(fold)
        self._path = os.path.join(path, PREDICTIONS_FILE)
        self.n_rows = 0
        os.makedirs(path, exist_ok=True)
        self.schema = _schema(
            self._names, {"period_minutes": int(period_minutes), **(meta or {})}
        )
        self._writer = pq.ParquetWriter(self._path, self.schema, compression=compression)

    @property
    def path(self):
        """Where the rows are being written."""
        return self._path

    def append(self, series, horizon, stamps, y, yhat, mu):
        """Write one ``(series, horizon)`` block as a row group.

        Parameters
        ----------
        series : str
            The series key; must be one of the declared names.
        horizon : int
            The lead these rows were scored at.
        stamps : sequence of int
            Row timestamps in ms, in time order.
        y : sequence of float
            Realized outcomes, aligned with ``stamps``.
        yhat : sequence of float
            The model's forecasts, aligned with ``stamps``.
        mu : float
            The constant benchmark forecast for this fold and series.

        Returns
        -------
        int
            Rows written by this call.
        """
        import pyarrow as pa

        key = str(series)
        if key not in self._index:
            raise ValueError(
                f"series {key!r} was not declared at construction "
                f"(declared: {', '.join(self._names)})"
            )
        n = len(stamps)
        if len(y) != n or len(yhat) != n:
            raise ValueError(
                f"block {key!r} lead {horizon}: {n} stamps, {len(y)} outcomes, "
                f"{len(yhat)} forecasts — the three must agree"
            )
        if n == 0:
            return 0
        code = self._index[key]
        batch = pa.record_batch(
            [
                pa.array(stamps, type=pa.int64()),
                pa.DictionaryArray.from_arrays(
                    pa.array([code] * n, type=pa.int32()),
                    pa.array(self._names, type=pa.string()),
                ),
                pa.array([self._fold] * n, type=pa.int16()),
                pa.array([int(horizon)] * n, type=pa.int16()),
                pa.array(yhat, type=pa.float32()),
                pa.array(y, type=pa.float32()),
                pa.array([float(mu)] * n, type=pa.float32()),
            ],
            schema=self.schema,
        )
        self._writer.write_batch(batch)
        self.n_rows += n
        return n

    def close(self):
        """Finish the file. Idempotent — a second call is a no-op."""
        if self._writer is not None:
            self._writer.close()
            self._writer = None
        return self._path

    def __enter__(self):
        """Enter the context; the file is already open."""
        return self

    def __exit__(self, *exc):
        """Close the file, whether or not the block loop raised."""
        self.close()
        return False


def find_predictions(run_dir):
    """Every prediction file one run directory holds, sorted by node.

    Parameters
    ----------
    run_dir : str
        A run directory (one walk-forward fold, or a standalone run).

    Returns
    -------
    list of str
        Paths under ``artifacts/<node>/``; empty when the run saved none
        — which is NOT the same as a run that scored nothing, so the
        caller must say which it found.

    Examples
    --------
    A fold whose one score node wrote its rows::

        len(find_predictions(fold))  # 1
    """
    root = os.path.join(run_dir, "artifacts")
    if not os.path.isdir(root):
        return []
    found = []
    for node in sorted(os.listdir(root)):
        path = os.path.join(root, node, PREDICTIONS_FILE)
        if os.path.isfile(path):
            found.append(path)
    return found


def read_predictions(run_dir):
    """Read a run's saved rows as a columnar mapping.

    Parameters
    ----------
    run_dir : str
        A run directory.

    Returns
    -------
    dict
        ``period_minutes`` plus one list per name in
        :data:`PREDICTION_COLUMNS`, concatenated over every prediction
        file the run holds. Empty when it holds none.

    Examples
    --------
    The rows a fold scored::

        len(read_predictions(fold)["ts"])  # 6216
    """
    paths = find_predictions(run_dir)
    if not paths:
        return {}
    import pyarrow.parquet as pq

    out = {name: [] for name in PREDICTION_COLUMNS}
    period = 1
    for path in paths:
        table = pq.read_table(path)
        meta = table.schema.metadata or {}
        raw = meta.get(b"period_minutes")
        if raw is not None:
            period = int(raw)
        for name in PREDICTION_COLUMNS:
            column = table.column(name)
            if name == "series":
                column = column.cast("string")
            out[name].extend(column.to_pylist())
    out["period_minutes"] = period
    return out


def read_prediction_series(run_dir):
    """Group a run's rows into the per-``(series, lead)`` unit a test takes.

    The shape every ADR-0067 statistic consumes, rebuilt from the rows
    rather than trusted from a summary: ``d`` is the squared-error loss
    gap against the fold's constant benchmark, ``q`` the benchmark's own
    MSPE on the same rows, and ``y``/``yhat``/``mu`` ride along for the
    tests that need the pairs themselves (a calibration slope, a
    per-timestamp cross-sectional correlation, a scramble null).

    Parameters
    ----------
    run_dir : str
        A run directory.

    Returns
    -------
    list of dict
        One entry per ``(series, lead)``, each with ``symbol`` (the
        series key, under the name the scorer already uses), ``lead``,
        ``fold``, ``h_steps``, ``q``, ``stamps``, ``d``, ``y``, ``yhat``
        and ``mu``. Empty when the run saved no rows.

    Examples
    --------
    Three names at one lead::

        len(read_prediction_series(fold))  # 3
    """
    from dskit.pipeline.stats import dm_loss_series

    rows = read_predictions(run_dir)
    if not rows:
        return []
    period = max(int(rows["period_minutes"]), 1)
    grouped = {}
    for i, series in enumerate(rows["series"]):
        key = (series, int(rows["horizon"][i]))
        unit = grouped.get(key)
        if unit is None:
            unit = grouped[key] = {
                "symbol": series,
                "lead": key[1],
                "fold": int(rows["fold"][i]),
                "h_steps": max(key[1] // period, 1),
                "stamps": [],
                "y": [],
                "yhat": [],
                "mu": float(rows["mu"][i]),
            }
        unit["stamps"].append(int(rows["ts"][i]))
        unit["y"].append(float(rows["y"][i]))
        unit["yhat"].append(float(rows["yhat"][i]))
    out = []
    for unit in grouped.values():
        n = len(unit["y"])
        mu = unit["mu"]
        unit["q"] = sum((v - mu) ** 2 for v in unit["y"]) / n if n else 0.0
        unit["d"] = dm_loss_series(unit["y"], unit["yhat"], mu=mu) if n else []
        out.append(unit)
    out.sort(key=lambda u: (u["lead"], u["symbol"]))
    return out
