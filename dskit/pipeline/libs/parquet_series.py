"""Many day-named parquet files of one onboarded stream, read as a point-in-time series (ADR-0243).

A tape of ticks, bars or an index is often stored as one parquet file per UTC day, a thousand
files or more in one stream. ``ParquetRows`` (ADR-0228) reads ONE file a key names, and the
keyed-table attach matches a date key; neither reads many files of one stream, and neither answers
"the last row strictly before this instant". This pack does both, on the platform's own payload
read seam, so a project with day-file tapes reads them by catalog name and never by path.

What is here:

- :class:`ParquetSeries` resolves ``(root, source, stream)`` through
  :func:`dskit.onboarding.payload_files`, keeps the files whose relpath matches a ``{day}``
  template, and offers two reads. :meth:`~ParquetSeries.load_span` returns every row of the
  days a span touches. :meth:`~ParquetSeries.prior` returns, per instant, the last row STRICTLY
  BEFORE it and its age, and reads only the days an age cap can reach, so a decade of per-second
  rows is never held.
- Every day file is read ONCE as bytes, hashed against the snapshot manifest, and parsed from
  those same verified bytes: the bytes that were checked are the bytes that were used, and a
  drifted file refuses the run. A file must hold an integer epoch-millisecond instant column in
  ascending order and only instants of the UTC day its name carries; anything else refuses by
  file name rather than answering wrongly, because ``prior`` relies on both.
- :func:`prior_index` is the one home of the strictly-before rule.
- :class:`StreamManifests` is a ``data`` node that fingerprints the snapshot hash of named
  streams. A transform that reads files itself sits outside the run identity (a transform has no
  fingerprint), so it takes this node's output as an input and calls
  :meth:`ParquetSeries.require_manifest`; the run identity then moves when the store moves, and a
  store that moved between the fingerprint and the run is refused.

The kind is named ``stream-manifests`` in :data:`NODE_KINDS` and is claimed only by the opt-in
:func:`register`, or reached by import path
(``dskit.pipeline.libs.parquet_series:StreamManifests``). Nothing registers at toolkit import.

numpy, pyarrow and the onboarding read seam are imported inside the methods that need them: the
purity gate keeps them out of the module level, and a tier-2 pack may name the onboarding seam
only at function depth.
"""

import bisect
import hashlib
import numbers
import re
from datetime import date, timedelta

from dskit.pipeline.node import DEFAULT_NODE_KINDS, Node, reject_unknown_params

__all__ = [
    "AGE_COLUMN",
    "NODE_KINDS",
    "ParquetSeries",
    "StreamManifests",
    "manifest_entry",
    "prior_index",
    "register",
    "stream_problems",
]

#: The name of the age column :meth:`ParquetSeries.prior` adds; no declared column may take it.
AGE_COLUMN = "age_ms"

_DAY = "{day}"
_DAY_MS = 86_400_000
_EPOCH_DAY = date(1970, 1, 1)
#: Instants and ages must stay inside this, so ``instant - age`` never wraps an int64.
_LIMIT_MS = 1 << 62
_ISO_DAY = r"([0-9]{4}-[0-9]{2}-[0-9]{2})"
_ENTRY_STRINGS = ("source", "stream", "manifest_sha256")


def prior_index(times, instants):
    """Return, per instant, the index of the last time STRICTLY BEFORE it, or -1 when none.

    The one home of the strictly-before rule: a row AT the instant is not yet known, so it is
    never returned, and among equal times the last one is.

    Parameters
    ----------
    times : numpy.ndarray
        Ascending instants (duplicates allowed).
    instants : numpy.ndarray
        The query instants, any order.

    Returns
    -------
    numpy.ndarray
        Integer indexes into ``times``, aligned to ``instants``; ``-1`` where no time is before.

    Examples
    --------
    The row at the instant is skipped::

        prior_index(numpy.array([10, 20, 30]), numpy.array([5, 10, 11, 31]))
        # -> array([-1, -1,  0,  2])
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


def stream_problems(name, spec):
    """List problems with one onboarded stream reference: exactly ``{"source", "stream"}`` strings.

    The one home of the check every node that names a stream applies to its params.

    Parameters
    ----------
    name : str
        How a refusal names the reference (``"streams['ticks']"``).
    spec : object
        The declared reference.

    Returns
    -------
    list of str
        One problem when ``spec`` is not exactly two non-empty strings; empty when it is.
    """
    if (not isinstance(spec, dict) or set(spec) != {"source", "stream"}
            or any(not isinstance(v, str) or not v for v in spec.values())):
        return [f"{name} must be exactly {{source, stream}} strings, got {spec!r}"]
    return []


def _is_integer(value):
    """Say whether ``value`` is an integer (numpy's included), never a bool."""
    return isinstance(value, numbers.Integral) and not isinstance(value, bool)


def _entry_problem(held):
    """Name what is wrong with a held manifest entry, or return None."""
    if not isinstance(held, dict) or any(not isinstance(held.get(k), str) or not held[k] for k in _ENTRY_STRINGS):
        return f"a manifest entry holds {list(_ENTRY_STRINGS)} as non-empty strings, got {held!r}"
    return None


class ParquetSeries:
    """Read the day-named parquet files of one onboarded stream as a time series.

    Construction resolves the stream and lists its day files (no file is read). A file is read,
    verified and parsed the first time a read needs it, and the days of the last window stay held
    until a window needs other days.

    Parameters
    ----------
    root : str
        The onboarding root.
    source : str
        The registered source.
    stream : str
        The stream holding the files.
    relpath_template : str
        The file's relpath with exactly one ``{day}`` (an ISO date, ``YYYY-MM-DD``) and no other
        brace, e.g. ``"{day}.parquet"`` or ``"ticks/{day}.parquet"``. A file the template matches
        whose date is not a calendar day is refused, never skipped.
    time_column : str
        The column holding each row's instant: integer epoch milliseconds, ascending within a
        file (duplicates allowed), every instant inside the UTC day the file names. It must be
        one of ``columns``.
    columns : list of str
        The columns to read: distinct names, none of them :data:`AGE_COLUMN`.

    Raises
    ------
    ValueError
        On a malformed template or column list, or a template-matching file that names no
        calendar day.
    AssetError
        From the platform, when the store, source or stream cannot be resolved.

    Examples
    --------
    The last reading strictly before an instant, at most a minute old::

        series = ParquetSeries("./ob", "index-ticks", "files", "{day}.parquet",
                               "calc_time_ms", ["calc_time_ms", "index_value"])
        got = series.prior(numpy.array([1788310800000]), max_age_ms=60000)
        got["index_value"][0], got["age_ms"][0]   # the value and its age, or nan, nan
    """

    def __init__(self, root, source, stream, relpath_template, time_column, columns):
        from dskit.onboarding.artifacts import payload_files

        head, tail = self._split_template(relpath_template)
        self.columns = self._checked_columns(time_column, columns)
        self.time_column = time_column
        got = payload_files(root, source, stream)
        pattern = re.compile(re.escape(head) + _ISO_DAY + re.escape(tail))
        self._files = {}
        for relpath, path in got["files"].items():
            match = pattern.fullmatch(relpath)
            if match:
                self._files[self._day_index(relpath, match.group(1))] = (relpath, path, got["sha256"][relpath])
        self._indexes = sorted(self._files)
        self._manifest = manifest_entry(got)
        self._held = {}
        self._window_key = None
        self._window = None

    @staticmethod
    def _split_template(template):
        """Return the text before and after the one ``{day}``, or raise."""
        if not isinstance(template, str) or template.count(_DAY) != 1:
            raise ValueError(f"relpath_template must carry exactly one {_DAY}, got {template!r}")
        head, tail = template.split(_DAY)
        if any(ch in head + tail for ch in "{}"):
            raise ValueError(f"relpath_template may carry only {_DAY} as a brace, got {template!r}")
        return head, tail

    @staticmethod
    def _checked_columns(time_column, columns):
        """Return ``columns`` as a list after the shape, uniqueness and reserved-name checks."""
        if (not isinstance(columns, (list, tuple)) or not columns
                or any(not isinstance(c, str) or not c for c in columns)):
            raise ValueError(f"columns must be a non-empty list of column names, got {columns!r}")
        duplicated = sorted({c for c in columns if list(columns).count(c) > 1})
        if duplicated:
            raise ValueError(f"columns holds duplicate name(s) {duplicated}")
        if AGE_COLUMN in columns:
            raise ValueError(f"a column may not be named {AGE_COLUMN!r}: prior() reports each row's age there")
        if time_column not in columns:
            raise ValueError(f"time_column {time_column!r} must be one of the columns {list(columns)}")
        return list(columns)

    @staticmethod
    def _day_index(relpath, text):
        """Return the days-since-epoch of an ISO date a relpath carries, or raise naming the file."""
        try:
            return (date.fromisoformat(text) - _EPOCH_DAY).days
        except ValueError:
            raise ValueError(f"{relpath} matches the template but {text!r} is not a calendar day") from None

    def manifest(self):
        """Return the identity of the snapshot being read.

        Returns
        -------
        dict
            A copy of :func:`manifest_entry` for the resolved stream.
        """
        return dict(self._manifest)

    def require_manifest(self, held, label=None):
        """Refuse a store that moved since ``held`` was fingerprinted: the one re-check rule.

        Parameters
        ----------
        held : dict
            A manifest entry, normally one of a :class:`StreamManifests` output.
        label : str, optional
            How a refusal names this tape; default ``"<source>/<stream>"``.

        Raises
        ------
        ValueError
            When ``held`` is not a manifest entry, names another source or stream, or carries
            another snapshot hash than the store holds now.
        """
        now = self._manifest
        name = label if label is not None else f"{now['source']}/{now['stream']}"
        problem = _entry_problem(held)
        if problem:
            raise ValueError(f"{name}: the held manifest is unusable: {problem}")
        if (held["source"], held["stream"]) != (now["source"], now["stream"]):
            raise ValueError(f"{name}: fingerprinted as {held['source']}/{held['stream']} "
                             f"but read as {now['source']}/{now['stream']}")
        if held["manifest_sha256"] != now["manifest_sha256"]:
            raise ValueError(f"{name}: the store moved since the manifest was fingerprinted "
                             f"({held['manifest_sha256'][:12]} then {now['manifest_sha256'][:12]}); rerun")

    def days(self):
        """List the ISO days that have a file, ascending.

        Returns
        -------
        list of str
            ``YYYY-MM-DD`` names.
        """
        return [(_EPOCH_DAY + timedelta(days=i)).isoformat() for i in self._indexes]

    def _verified_bytes(self, index):
        """Return a day file's bytes after checking them against the manifest digest, or raise."""
        relpath, path, digest = self._files[index]
        with open(path, "rb") as handle:
            raw = handle.read()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError(
                f"{relpath} no longer matches the manifest sha256 {digest}: the acquired file drifted; "
                "re-acquire or verify the source")
        return raw

    def _parse(self, index, raw):
        """Parse verified bytes into ``{column: array}``, refusing a non-parquet file or a missing column."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        relpath = self._files[index][0]
        try:
            parquet = pq.ParquetFile(pa.BufferReader(raw))
            present = parquet.schema_arrow.names
            if all(c in present for c in self.columns):
                table = parquet.read(columns=self.columns)
            else:
                table = None
        except pa.ArrowException as err:
            raise ValueError(f"{relpath}: cannot be read as parquet: {err}") from err
        if table is None:
            raise ValueError(f"{relpath} has no column(s) {[c for c in self.columns if c not in present]}")
        return {c: table.column(c).to_numpy() for c in self.columns}

    def _checked_times(self, index, times):
        """Return the instants as int64 after the integer, ascending and own-day checks."""
        import numpy as np

        relpath, name = self._files[index][0], self.time_column
        if times.dtype.kind not in "iu" or (times.dtype.kind == "u" and times.size and times.max() >= _LIMIT_MS):
            raise ValueError(f"{relpath}: {name} must be an integer epoch-ms column, got {times.dtype}")
        times = times.astype(np.int64, copy=False)
        if times.size > 1 and bool(np.any(times[1:] < times[:-1])):
            raise ValueError(f"{relpath}: {name} is not ascending")
        start = index * _DAY_MS
        if times.size and (times[0] < start or times[-1] >= start + _DAY_MS):
            raise ValueError(f"{relpath}: {name} holds instants outside the UTC day the file names")
        return times

    def _read_day(self, index):
        """Read one day file as verified bytes into checked ``{column: array}``."""
        arrays = self._parse(index, self._verified_bytes(index))
        arrays[self.time_column] = self._checked_times(index, arrays[self.time_column])
        return arrays

    def _between(self, first, last):
        """List the day indexes that have a file within ``[first, last]``, ascending."""
        return self._indexes[bisect.bisect_left(self._indexes, first):bisect.bisect_right(self._indexes, last)]

    def _stack(self, parts):
        """Concatenate day arrays column by column; no day gives empty arrays (time int64, the rest float64)."""
        import numpy as np

        out = {}
        for column in self.columns:
            pieces = [p[column] for p in parts]
            empty = np.int64 if column == self.time_column else np.float64
            out[column] = np.concatenate(pieces) if pieces else np.array([], dtype=empty)
        return out

    @staticmethod
    def _bound(name, value):
        """Return ``value`` as a Python int, or raise naming ``name``."""
        if not _is_integer(value):
            raise ValueError(f"{name} must be an integer epoch-ms, got {value!r}")
        return int(value)

    def load_span(self, first_ms, last_ms):
        """Return every row of the days that ``[first_ms, last_ms]`` touches.

        Parameters
        ----------
        first_ms, last_ms : int
            Epoch ms bounds, ``first_ms <= last_ms``; whole day files are returned, not a cut at
            the bounds. Cost follows the files that exist, never the days the span covers.

        Returns
        -------
        dict
            ``{column: numpy.ndarray}`` ascending by the time column, each column as stored; empty
            arrays when the store holds none of those days.

        Raises
        ------
        ValueError
            On bounds that are not ordered integers, a drifted file, a missing column, or
            instants that are not integers, not ascending, or outside their file's UTC day.
        """
        first, last = self._bound("first_ms", first_ms), self._bound("last_ms", last_ms)
        if first > last:
            raise ValueError(f"first_ms {first} must not be after last_ms {last}")
        indexes = self._between(first // _DAY_MS, last // _DAY_MS)
        return self._stack([self._read_day(i) for i in indexes])

    def _query_instants(self, instants_ms):
        """Return the query as a one-dimensional int64 array inside the safe range, or raise."""
        import numpy as np

        array = np.asarray(instants_ms)
        if array.ndim == 1 and array.size == 0:
            return np.empty(0, dtype=np.int64)
        if array.ndim != 1 or array.dtype.kind not in "iu":
            raise ValueError(f"instants_ms must be a one-dimensional integer array of epoch ms, "
                             f"got {array.dtype} with shape {array.shape}")
        unsigned = array.dtype.kind == "u"
        if array.max() >= _LIMIT_MS or (not unsigned and array.min() <= -_LIMIT_MS):
            raise ValueError(f"instants_ms must lie strictly within +-{_LIMIT_MS} ms")
        return array.astype(np.int64)

    @staticmethod
    def _query_cap(max_age_ms):
        """Return the age cap as a Python int in range, or raise."""
        if not _is_integer(max_age_ms) or not 0 <= int(max_age_ms) <= _LIMIT_MS:
            raise ValueError(f"max_age_ms must be an integer in [0, {_LIMIT_MS}], got {max_age_ms!r}")
        return int(max_age_ms)

    def _prior_window(self, first, last):
        """Return the numeric stacked days of ``[first, last]``, reading only days not held, cached by bounds."""
        if self._window_key == (first, last):
            return self._window
        indexes = self._between(first, last)
        self._held = {i: self._held[i] if i in self._held else self._read_day(i) for i in indexes}
        window = self._stack([self._held[i] for i in indexes])
        text = [c for c in self.columns if window[c].dtype.kind not in "biuf"]
        if text:
            self._window_key = None
            raise ValueError(f"prior() needs numeric columns, {text} hold {[str(window[c].dtype) for c in text]}")
        self._window, self._window_key = window, (first, last)
        return window

    def _fill(self, out, instants, positions, first, last, cap):
        """Answer the instants at ``positions`` from the window of days ``[first, last]``, in place."""
        import numpy as np

        window = self._prior_window(first, last)
        times = window[self.time_column]
        if times.size == 0:
            return
        asked = instants[positions]
        found = prior_index(times, asked)
        rows = np.maximum(found, 0)
        ages = asked - times[rows]
        keep = (found >= 0) & (ages <= cap)
        for column in self.columns:
            out[column][positions[keep]] = window[column][rows[keep]]
        out[AGE_COLUMN][positions[keep]] = ages[keep]

    def prior(self, instants_ms, max_age_ms):
        """Return, per instant, the last row strictly before it and no older than ``max_age_ms``.

        Only the day files an age cap can reach are read. A day with no file contributes
        nothing, so with a cap shorter than a day nothing is ever carried across it; with a longer
        cap the cap alone decides how far back a row may come from, and ``age_ms`` reports it.
        A day file is verified when it is read into the held window.

        Parameters
        ----------
        instants_ms : numpy.ndarray
            Query instants, a one-dimensional integer array (or list) of epoch ms, any order.
        max_age_ms : int
            The oldest a row may be: ``instant - row_time <= max_age_ms``, ``0 <= max_age_ms``.

        Returns
        -------
        dict
            ``{column: float64 array}`` aligned to the input order (the time column included)
            plus :data:`AGE_COLUMN`; NaN where no row qualifies. Integer columns are returned as
            float64, exact below 2**53.

        Raises
        ------
        ValueError
            On instants or a cap of the wrong type or range, a non-numeric column in a day it
            reads, or anything :meth:`load_span` refuses in the files it reads.

        Examples
        --------
        Three instants answered at once, in any order::

            got = series.prior(numpy.array([t + 50_500, t + 10_500, t + 99_999]), 10_000)
            got["v"]   # the last value strictly before each, aligned to the query
        """
        import numpy as np

        instants, cap = self._query_instants(instants_ms), self._query_cap(max_age_ms)
        out = {c: np.full(instants.size, np.nan) for c in [*self.columns, AGE_COLUMN]}
        if instants.size == 0:
            return out
        firsts, lasts = (instants - cap) // _DAY_MS, (instants - 1) // _DAY_MS
        order = np.lexsort((lasts, firsts))
        keys = np.stack((firsts[order], lasts[order]), axis=1)
        cuts = np.flatnonzero(np.any(keys[1:] != keys[:-1], axis=1)) + 1
        edges = [0, *cuts.tolist(), instants.size]
        for low, high in zip(edges[:-1], edges[1:]):
            self._fill(out, instants, order[low:high], int(keys[low, 0]), int(keys[low, 1]), cap)
        return out


class StreamManifests(Node):
    """Fingerprint the onboarded streams a transform reads by hand (role ``data``).

    Output ``manifests``: ``{name: manifest_entry(...)}``. The fingerprint is each stream's
    snapshot hash, so the run identity moves when a store moves. A node that takes this output as
    an input re-checks it against what it reads (:meth:`ParquetSeries.require_manifest`), and
    refuses a store that changed between the fingerprint and the run. One instance resolves each
    stream once, so the fingerprint and the output describe one snapshot.

    Parameters
    ----------
    params : dict
        ``root`` (str, REQUIRED) the onboarding root; ``streams`` (dict, REQUIRED, non-empty)
        ``{name: {"source": ..., "stream": ...}}``.

    Examples
    --------
    Pin two day-file streams::

        node = StreamManifests("streams", {"root": "./ob", "streams": {
            "ticks": {"source": "tick-archive", "stream": "files"},
            "index": {"source": "index-archive", "stream": "files"}}})
        node.fingerprint()
        # -> {"kind": "StreamManifests", "manifests": {"ticks": "<sha256>", "index": "<sha256>"}}
    """

    role = "data"
    outputs = ("manifests",)
    _PARAMS = ("root", "streams")

    def __init__(self, key, params=None, *, mode=None, artifact=""):
        super().__init__(key, params, mode=mode, artifact=artifact)
        self._snapshot = None

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
        if not isinstance(streams, dict) or not streams:
            problems.append(f"streams is required: a non-empty map name -> {{source, stream}}, got {streams!r}")
        else:
            for name, spec in streams.items():
                problems += stream_problems(f"streams[{name!r}]", spec)
        return problems

    def _manifests(self):
        """Resolve every stream once per instance, so resolve and execute see one snapshot."""
        from dskit.onboarding.artifacts import payload_files

        if self._snapshot is None:
            self._snapshot = {
                name: manifest_entry(payload_files(self.params["root"], spec["source"], spec["stream"]))
                for name, spec in self.params["streams"].items()}
        return self._snapshot

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
            ``{"manifests": {name: entry}}``, copies the caller may keep or change.
        """
        return {"manifests": {n: dict(m) for n, m in self._manifests().items()}}


#: The pack's kinds.
NODE_KINDS = (("stream-manifests", StreamManifests),)


def register(registry=None):
    """Claim the pack's kind names in ``registry`` (default the toolkit's).

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
