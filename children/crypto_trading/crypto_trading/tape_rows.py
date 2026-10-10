"""The day-file Binance tapes as the row lists ``MinuteFeatures`` takes.

``MinuteFeatures`` reads ``klines`` (and optionally ``bvol``) as lists of rows carrying an ``asset``; the tapes
are onboarded as one parquet file per UTC day (dskit's ``ParquetSeries`` reads them by source and stream, never
by path). This node is the thin join between the two: it reads every day an asset's tape holds and tags each row
with its asset. Like ``SpotFeatures`` it reads files itself, so it takes dskit's ``StreamManifests`` output as an
input (the data enters the run identity) and refuses a store that moved since the manifest was taken.

The column names are the document's, not this module's: which fields a row carries is a param, and a test pins
the lists the shipped document gives to the fields ``MinuteFeatures`` requires.

HELD IN THE CHILD, OWNER DECISION PENDING (2026-10-09). Reading every day of a tape into per-asset arrays or asset-tagged
rows, cut to a window and reduced per bucket, is generic, so it should graduate to dskit's ``parquet_series`` next
to ``ParquetSeries``. No ADR for that exists yet (the decision log holds none naming it), so this is the only copy:
delete this module when the capability is built there, and a second project that needs the same read must wait for
that, not copy this file.

Import cost: stdlib + dskit; numpy and pyarrow only inside the dskit read.
"""

from dskit.pipeline.libs.parquet_series import ParquetSeries, stream_problems
from dskit.pipeline.node import Node, check_int_param, reject_unknown_params
from dskit.production.base import parse_utc_ms

from .spot_features import tape_name

__all__ = ["TapeRows"]

_DAY_MS = 86_400_000
#: Days of one tape held in memory at a time. The span is only a memory bound (a window read in spans is the same
#: rows as one read), so it is a default, not a contract.
DEFAULT_SPAN_DAYS = 7
#: The two shapes a tape leaves in: the rows ``MinuteFeatures`` has always taken, or per-asset field arrays.
OUTPUTS = ("rows", "arrays")
DEFAULT_OUTPUT = "rows"
_TAPES = ("klines", "bvol")
_REQUIRED_TAPES = ("klines",)


class TapeRows(Node):
    """Read each asset's kline (and optional BVOL) tape into rows (role ``transform``).

    Inputs: ``manifests`` (a ``StreamManifests`` output holding one entry per tape, named
    ``<asset>_<tape>`` as :func:`~crypto_trading.spot_features.tape_name` spells it). Outputs: ``klines`` and
    ``bvol`` (lists of rows, each ``{"asset": ..., <the declared fields>}``, ascending by time within an asset)
    and ``provenance`` (per tape: the manifest read and the row count).

    Parameters
    ----------
    params : dict
        ``root`` (str, required) the onboarding root; ``assets`` (dict, required) ``{asset: {"klines":
        {"source", "stream"}, "bvol": {"source", "stream"}}}`` with ``bvol`` optional per asset;
        ``day_relpath_template`` (str with one ``{day}``, required); ``columns`` (dict, required)
        ``{"klines": {"time", "fields"}, "bvol": {"time", "fields"}}`` where ``time`` is the epoch-ms column
        (one of ``fields``) and ``fields`` the columns each row carries. Optional ``start_ms`` / ``end_ms``
        (ints >= 0, ``start_ms < end_ms``): keep only rows whose time column lies in ``[start_ms, end_ms)``
        and read only the day files that span touches, so a window's end is a reader bound, not a filter
        applied after the days were loaded. Optional ``bucket_last_ms`` (dict tape -> int >= 1): for that tape,
        drop rows with a non-finite field, then keep only the LAST row (by time, ties by the later fields) in
        each ``bucket_ms`` bucket ``[k*bucket_ms, (k+1)*bucket_ms)``. This is EXACT for a consumer that reads
        "the newest row strictly before an instant that is a multiple of ``bucket_ms``" (the BVOL level: the
        newest row before a bucket edge is the last row of the newest non-empty bucket before it, with its own
        time, so the age cap is unchanged) and it is what keeps a second-resolution tape (millions of rows a
        month) from being held as one Python dict per row. Optional ``span_days`` (int >= 1, default
        :data:`DEFAULT_SPAN_DAYS`): read each tape that many UTC days at a time, reducing each span before the
        next is read, so the peak is one span of the raw tape, not all of it. A bucket must divide a day, so a
        span edge is always a bucket edge.
        Optional ``output`` (``"rows"`` default, or ``"arrays"``): ``"arrays"`` leaves ``klines`` / ``bvol`` as
        ``{asset: {field: array}}``, which ``MinuteFeatures`` reads without a dict per row (a minute tape of
        years is millions of rows; as dicts it costs about 600 bytes a row).

    Examples
    --------
    Two assets, klines only::

        node = TapeRows("tapes", {
            "root": "./ob", "day_relpath_template": "{day}.parquet",
            "assets": {"AAA": {"klines": {"source": "k-aaa", "stream": "files"}},
                       "BBB": {"klines": {"source": "k-bbb", "stream": "files"}}},
            "columns": {"klines": {"time": "open_time_ms", "fields": ["open_time_ms", "close"]},
                        "bvol": {"time": "calc_time_ms", "fields": ["calc_time_ms", "index_value"]}}})
        out = node.run(None, {"manifests": manifests})
        # -> out["klines"][0] == {"asset": "AAA", "open_time_ms": ..., "close": ...}
    """

    role = "transform"
    outputs = ("klines", "bvol", "provenance")
    _PARAMS = ("root", "assets", "day_relpath_template", "columns", "start_ms", "end_ms", "bucket_last_ms", "span_days", "output")

    @classmethod
    def _assets_problems(cls, assets):
        """Problems with the ``assets`` map: tapes by name, each a source and a stream."""
        if not isinstance(assets, dict) or not assets:
            return [f"assets is required: a non-empty map asset -> tapes, got {assets!r}"]
        problems = []
        for asset, tapes in assets.items():
            if not isinstance(tapes, dict) or not set(_REQUIRED_TAPES) <= set(tapes) <= set(_TAPES):
                problems.append(f"assets[{asset!r}] must carry {list(_REQUIRED_TAPES)} and may carry "
                                f"{list(_TAPES)}, got {tapes!r}")
                continue
            for tape, spec in tapes.items():
                problems += stream_problems(f"assets[{asset!r}].{tape}", spec)
        return problems

    @classmethod
    def _columns_problems(cls, columns):
        """Problems with ``columns``: per tape a ``time`` that is one of its ``fields``."""
        if not isinstance(columns, dict) or set(columns) != set(_TAPES):
            return [f"columns is required: exactly the keys {list(_TAPES)}, got {columns!r}"]
        problems = []
        for tape, block in columns.items():
            fields = block.get("fields") if isinstance(block, dict) else None
            ok = (isinstance(block, dict) and set(block) == {"time", "fields"} and isinstance(fields, list)
                  and fields and all(isinstance(f, str) and f for f in fields) and len(set(fields)) == len(fields))
            if not ok:
                problems.append(f"columns.{tape} must be {{time, fields}} with fields a list of distinct names, "
                                f"got {block!r}")
            elif block["time"] not in fields:
                problems.append(f"columns.{tape}.time {block['time']!r} must be one of its fields {fields}")
        return problems

    @classmethod
    def _bucket_problems(cls, bucket):
        """Problems with ``bucket_last_ms``: a map from a declared tape to a positive int of milliseconds."""
        if bucket is None:
            return []
        if not isinstance(bucket, dict) or not set(bucket) <= set(_TAPES):
            return [f"bucket_last_ms must map tapes in {list(_TAPES)} to ms, got {bucket!r}"]
        problems = []
        for tape, width in bucket.items():
            check_int_param(problems, f"bucket_last_ms[{tape!r}]", width, ge=1)
        return problems

    @classmethod
    def _span_problems(cls, params):
        """Problems with ``span_days`` and with a bucket that does not divide a day."""
        problems = []
        if "span_days" in params:
            check_int_param(problems, "span_days", params["span_days"], ge=1)
        bucket = params.get("bucket_last_ms")
        for tape, width in (bucket.items() if isinstance(bucket, dict) else ()):
            if isinstance(width, int) and not isinstance(width, bool) and width >= 1 and _DAY_MS % width:
                problems.append(f"bucket_last_ms[{tape!r}] must divide a day ({_DAY_MS} ms) so a span edge is a bucket edge, got {width}")
        return problems

    @classmethod
    def _bounds_problems(cls, params):
        """Problems with ``start_ms`` / ``end_ms``: non-negative ints, start before end."""
        problems = []
        for knob in ("start_ms", "end_ms"):
            if knob in params:
                check_int_param(problems, knob, params[knob], ge=0)
        if not problems and {"start_ms", "end_ms"} <= set(params) and params["start_ms"] >= params["end_ms"]:
            problems.append(f"start_ms must be before end_ms, got {params['start_ms']} and {params['end_ms']}")
        return problems

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
            One problem per unknown, missing or unusable knob.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        if not isinstance(params.get("root"), str) or not params.get("root"):
            problems.append(f"root is required: the onboarding root, got {params.get('root')!r}")
        problems += cls._assets_problems(params.get("assets"))
        problems += cls._columns_problems(params.get("columns"))
        problems += cls._bounds_problems(params)
        problems += cls._bucket_problems(params.get("bucket_last_ms"))
        problems += cls._span_problems(params)
        if params.get("output", DEFAULT_OUTPUT) not in OUTPUTS:
            problems.append(f"output must be one of {list(OUTPUTS)}, got {params.get('output')!r}")
        template = params.get("day_relpath_template")
        if not isinstance(template, str) or template.count("{day}") != 1:
            problems.append(f"day_relpath_template is required: a file name with one {{day}}, got {template!r}")
        return problems

    def validate_inputs(self, inputs):
        """Refuse a ``manifests`` port that is not a dict.

        Parameters
        ----------
        inputs : dict
            The wired ports.

        Returns
        -------
        list of str
            One problem when the port is not a ``StreamManifests`` output.
        """
        if isinstance(inputs.get("manifests"), dict):
            return []
        return [f"manifests must be a StreamManifests output, got {type(inputs.get('manifests')).__name__}"]

    @staticmethod
    def _last_per_bucket(arrays, column, width):
        """Keep the last finite row of each ``width``-ms bucket; rows ordered by time then the later fields."""
        import numpy as np

        names = column["fields"]
        keep = np.ones(arrays[column["time"]].size, dtype=bool)
        for name in names:
            keep &= np.isfinite(arrays[name].astype(float))
        arrays = {name: arrays[name][keep] for name in names}
        order = np.lexsort(tuple(arrays[name] for name in reversed(names)))  # time first, then the fields in order
        arrays = {name: arrays[name][order] for name in names}
        bucket = arrays[column["time"]] // width
        last = np.ones(bucket.size, dtype=bool)
        last[:-1] = bucket[1:] != bucket[:-1]
        return {name: arrays[name][last] for name in names}

    @staticmethod
    def _joined(pieces, names):
        """Concatenate the spans' arrays field by field."""
        import numpy as np

        return {name: np.concatenate([piece[name] for piece in pieces]) for name in names}

    def _rows(self, asset, tape, held):
        """Read every day of one tape, after refusing a store that moved; return ``(rows or arrays, manifest)``."""
        spec = self.params["assets"][asset][tape]
        column = self.params["columns"][tape]
        series = ParquetSeries(self.params["root"], spec["source"], spec["stream"],
                               self.params["day_relpath_template"], column["time"], column["fields"])
        name = tape_name(asset, tape)
        if name not in held:
            raise ValueError(f"{self.key}: the manifests input has no {name!r}; wire a StreamManifests that names it")
        series.require_manifest(held[name], label=f"{self.key}: {name}")
        days = series.days()
        if not days:
            return [], series.manifest()
        first, last = (parse_utc_ms(f"{d}T00:00:00+00:00") for d in (days[0], days[-1]))
        start, end = self.params.get("start_ms", first), self.params.get("end_ms", last + _DAY_MS)
        first, last = max(first, start), min(last + _DAY_MS, end) - 1
        if first > last:
            return [], series.manifest()
        width = (self.params.get("bucket_last_ms") or {}).get(tape)
        step = self.params.get("span_days", DEFAULT_SPAN_DAYS) * _DAY_MS
        pieces = []
        for lo in range(first - first % _DAY_MS, last + 1, step):  # span edges are UTC day edges
            got = series.load_span(max(lo, first), min(lo + step - 1, last))
            keep = (got[column["time"]] >= start) & (got[column["time"]] < end)
            arrays = {f: got[f][keep] for f in column["fields"]}
            pieces.append(self._last_per_bucket(arrays, column, width) if width else arrays)
        arrays = self._joined(pieces, column["fields"])
        if self.params.get("output", DEFAULT_OUTPUT) == "arrays":
            return arrays, series.manifest()
        lists = [arrays[f].tolist() for f in column["fields"]]
        rows = [{"asset": asset, **dict(zip(column["fields"], values))} for values in zip(*lists)]
        return rows, series.manifest()

    def run(self, ctx, inputs):
        """Read every declared tape.

        Parameters
        ----------
        ctx : object
            The run context (unused).
        inputs : dict
            ``manifests``: the fingerprinted streams.

        Returns
        -------
        dict
            ``klines``, ``bvol`` (rows tagged with their asset, or ``{asset: {field: array}}`` when ``output`` is
            ``"arrays"``) and ``provenance`` (``{tape name: {"manifest",
            "rows"}}``).

        Raises
        ------
        ValueError
            When the manifests lack a tape this node reads, when the store moved since they were taken, or on
            anything dskit's day-file reader refuses (a drifted file, a missing column).
        """
        arrays = self.params.get("output", DEFAULT_OUTPUT) == "arrays"
        out = {tape: {} if arrays else [] for tape in _TAPES}
        provenance = {}
        for asset, tapes in self.params["assets"].items():
            for tape in tapes:
                rows, manifest = self._rows(asset, tape, inputs["manifests"])
                if arrays:
                    out[tape].update({asset: rows} if rows else {})
                    count = len(rows[self.params["columns"][tape]["time"]]) if rows else 0
                else:
                    out[tape] += rows
                    count = len(rows)
                provenance[tape_name(asset, tape)] = {"manifest": manifest, "rows": count}
        return {**out, "provenance": provenance}

