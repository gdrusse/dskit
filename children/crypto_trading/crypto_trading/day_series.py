"""Daily parquet files of one onboarded stream, read as a time series.

INTERIM HOME (PROPOSED ADR-0240): dskit's ``ParquetRows`` reads ONE file of a stream (a key
names it), and the Binance sources hold one parquet per UTC day, a thousand files per stream.
No node reads many files of one stream, nor answers "the last row strictly before this
instant". This module does both for day-named files; a generic reader belongs in dskit and
these classes and tests move there.

- :class:`ParquetDaySeries` resolves ``(root, source, stream)`` through ``payload_files``,
  keeps the files whose relpath matches a ``{day}`` template, verifies each file's bytes against
  the snapshot manifest before parsing it (a drifted file refuses the run), and offers two reads:
  :meth:`~ParquetDaySeries.load_span` (every row of the days a span touches) and
  :meth:`~ParquetDaySeries.prior` (the last row STRICTLY BEFORE each instant, with its age,
  loading only the days an age cap can reach, so a decade of per-second rows is never held).
  Instants inside a file must be ascending; duplicates are allowed, a step backwards is refused.
- :func:`prior_index` is the one home of the strictly-before rule.
- :class:`StreamManifests` is a ``data`` node that fingerprints the streams a later node reads
  by hand, so the run identity moves when the store does (a transform that reads files itself
  would otherwise sit outside the identity hash).

pyarrow and numpy are imported inside the methods that read.

Import cost: stdlib + dskit.
"""

import re
from datetime import date, timedelta

from dskit.onboarding import file_digest, payload_files
from dskit.pipeline.node import Node, reject_unknown_params

__all__ = ["ParquetDaySeries", "StreamManifests", "manifest_entry", "prior_index", "tape_name"]

_DAY = "{day}"
_DAY_MS = 86_400_000
_EPOCH_DAY = date(1970, 1, 1)


def tape_name(asset, tape):
    """Name the manifest of an asset's ``tape`` (``klines`` or ``bvol``): the key both nodes use.

    Parameters
    ----------
    asset : str
        The asset key (``BTC``).
    tape : str
        The kind of series.

    Returns
    -------
    str
        ``"<asset>_<tape>"``.
    """
    return f"{asset}_{tape}"


def prior_index(times, instants):
    """Return, per instant, the index of the last time STRICTLY BEFORE it, or -1 when none.

    Parameters
    ----------
    times : numpy.ndarray
        Ascending instants (duplicates allowed).
    instants : numpy.ndarray
        The query instants, any order.

    Returns
    -------
    numpy.ndarray
        Integer indexes into ``times``; a row AT the instant is not yet known, so it is
        never returned.
    """
    import numpy as np

    return np.searchsorted(times, instants, side="left") - 1


def manifest_entry(got):
    """Describe a ``payload_files`` result: the identity a run records and a reader re-checks.

    Parameters
    ----------
    got : dict
        What :func:`dskit.onboarding.payload_files` returned.

    Returns
    -------
    dict
        ``source``, ``stream``, ``snapshot``, ``manifest_sha256`` and ``files`` (the file count).
    """
    return {"source": got["source"], "stream": got["stream"], "snapshot": got["snapshot"],
            "manifest_sha256": got["manifest_sha256"], "files": len(got["files"])}


def _day_name(index):
    """Return the ISO date of a day index (days since the epoch)."""
    return (_EPOCH_DAY + timedelta(days=int(index))).isoformat()


class ParquetDaySeries:
    """Read the day-named parquet files of one stream as a time series.

    Parameters
    ----------
    root : str
        The onboarding root.
    source : str
        The registered source.
    stream : str
        The stream holding the files.
    relpath_template : str
        The file name with one ``{day}`` (an ISO date), e.g. ``"{day}.parquet"``.
    time_column : str
        The column holding each row's instant (epoch ms); it must be among ``columns``.
    columns : list of str
        The columns to read.

    Examples
    --------
    The last BVOL reading strictly before an instant, at most a minute old::

        series = ParquetDaySeries("./ob", "binance-btcbvol", "files", "{day}.parquet",
                                  "calc_time_ms", ["calc_time_ms", "index_value"])
        got = series.prior(numpy.array([1788310800000]), max_age_ms=60000)
        got["index_value"][0], got["age_ms"][0]   # the value and how old it is, or nan, nan
    """

    def __init__(self, root, source, stream, relpath_template, time_column, columns):
        if relpath_template.count(_DAY) != 1:
            raise ValueError(f"relpath_template must carry exactly one {_DAY}, got {relpath_template!r}")
        head, tail = relpath_template.split(_DAY)
        if any(ch in head + tail for ch in "{}"):
            raise ValueError(f"relpath_template may carry only {_DAY} as a placeholder, got {relpath_template!r}")
        if time_column not in columns:
            raise ValueError(f"time_column {time_column!r} must be one of the columns {list(columns)}")
        self.time_column = time_column
        self.columns = list(columns)
        got = payload_files(root, source, stream)
        pattern = re.compile(re.escape(head) + r"(\d{4}-\d{2}-\d{2})" + re.escape(tail))
        self._files = {}
        for relpath, path in got["files"].items():
            match = pattern.fullmatch(relpath)
            if match:
                self._files[match.group(1)] = (relpath, path, got["sha256"][relpath])
        self._manifest = manifest_entry(got)
        self._held_days = {}
        self._window_key = None
        self._window = None

    def manifest(self):
        """Return the identity of the snapshot being read (see :func:`manifest_entry`)."""
        return dict(self._manifest)

    def days(self):
        """List the ISO days that have a file, ascending."""
        return sorted(self._files)

    def _read_day(self, day):
        """Read one verified day file into ``{column: array}``; refuse drift, a missing column or disorder."""
        import numpy as np
        import pyarrow.parquet as pq

        relpath, path, digest = self._files[day]
        if file_digest(str(path)) != digest:
            raise ValueError(
                f"{relpath} no longer matches the manifest sha256 {digest}: the acquired file drifted; "
                "re-acquire or verify the source")
        present = pq.read_schema(path).names
        missing = [c for c in self.columns if c not in present]
        if missing:
            raise ValueError(f"{relpath} has no column(s) {missing}")
        table = pq.read_table(path, columns=self.columns)
        arrays = {c: table.column(c).to_numpy() for c in self.columns}
        if np.any(np.diff(arrays[self.time_column]) < 0):
            raise ValueError(f"{relpath}: {self.time_column} is not ascending")
        return arrays

    def _day(self, day):
        """Return a day's arrays, reading it once while it stays in the held set."""
        if day not in self._held_days:
            self._held_days[day] = self._read_day(day)
        return self._held_days[day]

    def _concat(self, first, last):
        """Concatenate the available days from index ``first`` to ``last`` inclusive, ascending."""
        import numpy as np

        names = [_day_name(i) for i in range(first, last + 1) if _day_name(i) in self._files]
        parts = [self._day(n) for n in names]
        out = {}
        for column in self.columns:
            pieces = [p[column] for p in parts]
            out[column] = np.concatenate(pieces) if pieces else np.array([], dtype=np.float64)
        times = out[self.time_column]
        if np.any(np.diff(times) < 0):
            raise ValueError(f"days {names[0]}..{names[-1]}: {self.time_column} is not ascending across files")
        for stale in [d for d in self._held_days if d not in names]:
            del self._held_days[stale]
        return out

    def load_span(self, first_ms, last_ms):
        """Return every row of the days that ``[first_ms, last_ms]`` touches.

        Parameters
        ----------
        first_ms, last_ms : int
            Epoch ms bounds; whole day files are returned, not a cut at the bounds.

        Returns
        -------
        dict
            ``{column: numpy.ndarray}`` ascending by the time column; empty arrays when the
            store holds none of those days.

        Raises
        ------
        ValueError
            On a drifted file, a missing column or instants out of order.
        """
        return self._concat(int(first_ms) // _DAY_MS, int(last_ms) // _DAY_MS)

    def prior(self, instants_ms, max_age_ms):
        """Return, per instant, the last row strictly before it and no older than ``max_age_ms``.

        Parameters
        ----------
        instants_ms : numpy.ndarray
            Query instants (epoch ms), any order.
        max_age_ms : int
            The oldest a row may be: ``instant - row_time <= max_age_ms``.

        Returns
        -------
        dict
            ``{column: float array}`` aligned to the input order (the time column included)
            plus ``age_ms``; NaN where no row qualifies. A day without a file is a gap: nothing
            is carried across it.
        """
        import numpy as np

        instants = np.asarray(instants_ms, dtype=np.int64)
        out = {c: np.full(instants.size, np.nan) for c in [*self.columns, "age_ms"]}
        for position in np.argsort(instants, kind="stable"):
            instant = int(instants[position])
            first, last = (instant - int(max_age_ms)) // _DAY_MS, (instant - 1) // _DAY_MS
            if self._window_key != (first, last):
                self._window, self._window_key = self._concat(first, last), (first, last)
            times = self._window[self.time_column]
            index = int(prior_index(times, instant))
            if index >= 0 and instant - int(times[index]) <= max_age_ms:
                for column in self.columns:
                    out[column][position] = self._window[column][index]
                out["age_ms"][position] = instant - int(times[index])
        return out


class StreamManifests(Node):
    """Fingerprint the onboarded streams a transform reads by hand (role ``data``).

    Output ``manifests``: ``{name: manifest_entry(...)}``. The fingerprint is each stream's
    snapshot hash, so the run identity moves when a store moves. A node that takes this output
    as an input re-checks it against what it reads, and refuses a store that changed between
    the fingerprint and the run.

    Parameters
    ----------
    params : dict
        ``root`` (str, REQUIRED) the onboarding root; ``streams`` (dict, REQUIRED)
        ``{name: {"source": ..., "stream": ...}}``.

    Examples
    --------
    Pin two Binance streams::

        node = StreamManifests("streams", {"root": "./ob", "streams": {
            "BTC_klines": {"source": "binance-btcusdt-1m", "stream": "files"}}})
        node.fingerprint()
        # -> {"kind": "StreamManifests", "manifests": {"BTC_klines": "<sha256>"}}
    """

    role = "data"
    outputs = ("manifests",)
    _PARAMS = ("root", "streams")
    _snap = None

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
        streams = params.get("streams")
        if not isinstance(streams, dict):
            problems.append(f"streams is required: a map name -> {{source, stream}}, got {streams!r}")
        else:
            for name, spec in streams.items():
                if (not isinstance(spec, dict) or set(spec) != {"source", "stream"}
                        or any(not isinstance(v, str) or not v for v in spec.values())):
                    problems.append(f"streams[{name!r}] must be exactly {{source, stream}} strings, got {spec!r}")
        return problems

    def _manifests(self):
        """Resolve every stream once per instance, so resolve and execute see one snapshot."""
        if self._snap is None:
            self._snap = {
                name: manifest_entry(payload_files(self.params["root"], spec["source"], spec["stream"]))
                for name, spec in self.params["streams"].items()}
        return self._snap

    def fingerprint(self):
        """Answer each stream's snapshot hash.

        Returns
        -------
        dict
            ``{"kind", "manifests": {name: manifest_sha256}}``.
        """
        return {"kind": type(self).__name__,
                "manifests": {n: m["manifest_sha256"] for n, m in self._manifests().items()}}

    def run(self, ctx, inputs):
        """Emit the manifests the fingerprint saw.

        Parameters
        ----------
        ctx : NodeContext
            Unused.
        inputs : dict
            Empty: role ``data`` takes no inputs.

        Returns
        -------
        dict
            ``{"manifests": {...}}``.
        """
        return {"manifests": {n: dict(m) for n, m in self._manifests().items()}}
