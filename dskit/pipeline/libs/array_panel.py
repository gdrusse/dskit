"""The tier-2 array-panel pack: one memory-mapped panel, bands cut in one place, windows gathered by index.

A model zoo or a feature-selection study over millions of minute rows cannot hold one Python dict
per row, and cannot materialise a lookback window as columns (a 1440-bar window is 1440 times the
data). This pack is the ONE shared data kind that avoids both: a directory of ``.npy`` files
written once, opened memory-mapped and read-only by every lane, with windows addressed by global
row index at gather time. It is domain-neutral: nothing here names a venue, a feature, a horizon
or a column.

**The unit contract.** Labels are stored AS GIVEN. They arrive already divided by the reference
(for a vol-standardised log-return label, ``y_h = ln(close(t+h) / close(t)) / scale``). The
``reference`` column is stored only so a scale-model baseline can read it, and is NEVER applied to
labels, so nothing downstream divides twice. The contract is also recorded in the manifest.

**On disk** (:class:`ArrayPanelWrite`): ``manifest.json`` plus ``features.npy`` (float32, Fortran
order so one column is contiguous), ``labels.npy`` ``(N, H)``, ``time.npy`` (epoch ms),
``group.npy``, ``reference.npy`` and ``run.npy`` (how many consecutive ``step_ms`` bars end at
each row, per group, computed once). Rows are sorted by group then time. The directory is built
beside its destination and renamed into place, so a reader never sees a half-written panel; the
manifest carries each file's sha256 so the directory registers as a ``localblobs`` stream
(ADR-0225) and is read back through the store (:meth:`ArrayPanel.from_source`).

**The four read-side objects**, each with one job:

* :class:`ColumnSelector` -- which feature columns an arm uses; an ablation arm is just a selector.
* :class:`PanelBands` -- the ONLY place fit / calibration / validation rows are cut, so training
  and scoring cannot disagree. The purge is :func:`dskit.pipeline.kinds_split.label_reaches`,
  imported, not restated.
* :class:`PanelScaler` -- chunked mean/std over the memmap, fit on the fit band only.
* :class:`DevicePanel` -- uploads ONLY the selected columns, in chunks, straight from the memmap,
  and gathers ``(B, F)`` rows and ``(B, L, C)`` windows from global row indices.

**Gap safety.** A window is only valid if every bar in it is exactly ``step_ms`` after the last,
that is ``run >= (window - 1) * stride + 1`` at the target row. ``min_run`` is one panel-level
setting of :class:`PanelBands` (which drops every row below it, and every row whose label bar
is past a forward gap). :class:`DevicePanel` takes the bands, not a number, and refuses a window
longer than ``bands.min_run``; ``gather`` accepts only rows inside the caller's spans and checks
``run`` at each target row, so no window reaches back across a gap or into unloaded history.

Wiring is by import path: :data:`NODE_KINDS` is empty. numpy and torch are imported only inside
methods.
"""

import hashlib
import json
import os
import shutil
from collections import namedtuple

from dskit.pipeline.document import is_node_ref
from dskit.pipeline.kinds_split import label_reaches
from dskit.pipeline.node import Node, atomic_write, check_int_param, reject_unknown_params

__all__ = [
    "ArrayPanel",
    "ArrayPanelWrite",
    "ColumnSelector",
    "DAY_MS",
    "DEFAULT_CHUNK_ROWS",
    "DEFAULT_MISSING",
    "DevicePanel",
    "MANIFEST_NAME",
    "MISSING_STRATEGIES",
    "NODE_KINDS",
    "PanelBands",
    "PanelBatch",
    "PanelScaler",
    "UNIT_CONTRACT",
]

#: Milliseconds in a day: the unit ``cal_days`` is stated in, and the only place it is defined.
DAY_MS = 86_400_000
#: Rows read from the memmap per step when scanning or uploading; bounds host memory.
DEFAULT_CHUNK_ROWS = 262_144
#: What :class:`PanelScaler` does with a missing value unless told otherwise.
DEFAULT_MISSING = "refuse"
#: The strategies for a missing feature value: refuse the run, or zero-fill after scaling.
MISSING_STRATEGIES = ("refuse", "mean")
#: The file inside a panel directory that describes the rest.
MANIFEST_NAME = "manifest.json"
#: The scale a constant column is given, so scaling it yields zeros instead of dividing by zero.
CONSTANT_COLUMN_STD = 1.0
#: The on-disk layout version, recorded in the manifest.
FORMAT_VERSION = 1
#: The unit statement recorded in every manifest (see the module docstring).
UNIT_CONTRACT = (
    "labels are stored as given, already divided by the reference; "
    "reference is stored for scale-model baselines and is never applied to labels"
)
#: Panel file name -> (npy file, dtype name). One table, read by the writer and the reader.
_FILES = {
    "features": ("features.npy", "float32"),
    "labels": ("labels.npy", "float64"),
    "time": ("time.npy", "int64"),
    "group": ("group.npy", "int64"),
    "reference": ("reference.npy", "float64"),
    "run": ("run.npy", "int32"),
}
_SOURCE_KEYS = ("root", "source", "stream", "relpath", "verify")
_SELECTOR_KEYS = ("names", "prefixes", "except_names", "except_prefixes")
_BAND_NAMES = ("fit", "cal", "val")

#: One gathered batch: flat rows, windows (``None`` without channels), labels, reference, group.
PanelBatch = namedtuple("PanelBatch", "x seq y ref group")


def _row_count(sel, total):
    """Return how many rows a slice or an index array selects out of ``total``."""
    return len(range(*sel.indices(total))) if isinstance(sel, slice) else len(sel)


def _read_columns(panel, columns, sel):
    """Return the float32 ``(rows, len(columns))`` block of the selected feature columns."""
    import numpy as np

    n = _row_count(sel, len(panel.time))
    if not columns:
        return np.zeros((n, 0), dtype=np.float32)
    return np.stack([np.asarray(panel.features[sel, c], dtype=np.float32) for c in columns], axis=1)


def _str_list_problem(name, value):
    """Return a problem unless ``value`` is a list of non-empty strings, else None."""
    if isinstance(value, (list, tuple)) and all(isinstance(v, str) and v for v in value):
        return None
    return f"{name} must be a list of non-empty strings, got {value!r}"


class ArrayPanelWrite(Node):
    """Write sorted equal-length columns as one memory-mappable panel directory.

    Role ``transform``. Input port ``columns``: a mapping of column name to an equal-length 1-D
    array, sorted by group then time. The unit contract (module docstring) applies: label columns
    are stored as given, already divided by the reference.

    Parameters
    ----------
    params : dict
        ``time`` (str, required): epoch-ms column. ``group`` (str, required): integer group code
        column. ``reference`` (str, required): positive reference column, stored not applied.
        ``label_prefix`` (str, required) and ``horizons`` (strictly increasing list of ints
        ``>= 1``, required): label columns are ``<label_prefix><h>`` for each ``h``.
        ``step_ms`` (int ``>= 1``, required): the bar length. ``path`` (str, required): the panel
        directory to create; it must not exist. ``drop`` (list of str, default ``[]``): columns
        left out of the features. ``nullable`` (list of str, default ``[]``): feature columns where
        NaN is allowed. Every other column is a feature, in the order given.

    Examples
    --------
    Write a two-bar, one-group panel with one horizon::

        node = ArrayPanelWrite("write", {
            "time": "t", "group": "g", "reference": "ref", "label_prefix": "y_",
            "horizons": [1], "step_ms": 60000, "path": "/store/panel"})
        out = node.run(ctx, {"columns": {
            "t": t, "g": g, "ref": ref, "y_1": y, "f0": f0}})
        out["manifest"]["files"]["features.npy"]   # -> the file's sha256
    """

    role = "transform"
    outputs = ("manifest", "path")

    _PARAMS = ("time", "group", "reference", "label_prefix", "horizons", "step_ms", "path",
               "drop", "nullable")
    _REQUIRED_STR = ("time", "group", "reference", "label_prefix", "path")
    DEFAULT_DROP = ()
    DEFAULT_NULLABLE = ()

    @classmethod
    def validate_params(cls, params):
        """Default-deny the knobs and hold each to its type.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            One problem per broken knob.
        """
        problems = super().validate_params(params)
        reject_unknown_params(problems, params, cls._PARAMS)
        for knob in cls._REQUIRED_STR:
            value = params.get(knob)
            if knob not in params:
                problems.append(f"{knob} is required")
            elif not is_node_ref(value) and not (isinstance(value, str) and value):
                problems.append(f"{knob} must be a non-empty string, got {value!r}")
        if "step_ms" not in params:
            problems.append("step_ms is required")
        elif not is_node_ref(params["step_ms"]):
            check_int_param(problems, "step_ms", params["step_ms"], ge=1)
        cls._horizon_problems(problems, params)
        for knob, default in (("drop", cls.DEFAULT_DROP), ("nullable", cls.DEFAULT_NULLABLE)):
            value = params.get(knob, default)
            problem = None if is_node_ref(value) else _str_list_problem(knob, value)
            if problem:
                problems.append(problem)
        return problems

    @classmethod
    def _horizon_problems(cls, problems, params):
        """Require ``horizons``: a non-empty, strictly increasing list of ints >= 1."""
        if "horizons" not in params:
            problems.append("horizons is required")
            return
        value = params["horizons"]
        if is_node_ref(value):
            return
        ok = (isinstance(value, (list, tuple)) and value
              and all(isinstance(h, int) and not isinstance(h, bool) and h >= 1 for h in value)
              and all(a < b for a, b in zip(value, value[1:])))
        if not ok:
            problems.append(f"horizons must be a strictly increasing list of ints >= 1, got {value!r}")

    def run(self, ctx, inputs):
        """Validate the columns, write the panel directory, and return its manifest.

        Parameters
        ----------
        ctx : NodeContext
            The run frame (unused).
        inputs : dict
            ``columns``: name -> 1-D array, all the same length.

        Returns
        -------
        dict
            ``manifest`` (the dict stored in ``manifest.json``, with per-file sha256) and ``path``.

        Raises
        ------
        ValueError
            Unsorted or duplicate (group, time); non-finite time/group/reference; a reference
            ``<= 0``; NaN or inf in a non-nullable feature; ragged columns; a missing column; a
            stray column starting with ``label_prefix``; or a destination that already exists.
        """
        import numpy as np

        arrays = self._arrays(np, inputs)
        time, group, reference = self._keys(np, arrays)
        labels = self._labels(np, arrays)
        names = self._feature_names(arrays)
        self._check_features(np, arrays, names)
        return self._publish(np, arrays, names, time, group, reference, labels)

    def _arrays(self, np, inputs):
        """Return the input columns as 1-D arrays of one length, or raise."""
        columns = inputs.get("columns") if isinstance(inputs, dict) else None
        if not isinstance(columns, dict) or not columns:
            raise ValueError(f"{self.key}: input 'columns' must be a non-empty mapping of name -> array")
        arrays = {name: np.asarray(values) for name, values in columns.items()}
        if any(a.ndim != 1 for a in arrays.values()):
            raise ValueError(f"{self.key}: every column must be 1-D")
        lengths = {len(a) for a in arrays.values()}
        if len(lengths) != 1 or lengths == {0}:
            raise ValueError(f"{self.key}: columns must be non-empty and the same length, got {sorted(lengths)}")
        required = [self.params[k] for k in ("time", "group", "reference")] + self._label_names()
        missing = [name for name in required if name not in arrays]
        if missing:
            raise ValueError(f"{self.key}: input columns are missing {missing}")
        if len({self.params[k] for k in ("time", "group", "reference")}) != 3:
            raise ValueError(f"{self.key}: time, group and reference must be three different columns")
        return arrays

    def _label_names(self):
        """Return the label column names, one per horizon, in horizon order."""
        return [f"{self.params['label_prefix']}{h}" for h in self.params["horizons"]]

    @staticmethod
    def _integer_column(np, name, values):
        """Return a finite, integral column as int64, or raise."""
        if values.dtype.kind in "iu":
            return values.astype(np.int64)
        values = values.astype(np.float64)
        if not np.isfinite(values).all() or not np.array_equal(values, np.floor(values)):
            raise ValueError(f"{name} must be finite integers (non-finite or fractional value found)")
        return values.astype(np.int64)

    def _keys(self, np, arrays):
        """Return the checked ``(time, group, reference)`` arrays; rows must be strictly sorted."""
        time = self._integer_column(np, self.params["time"], arrays[self.params["time"]])
        group = self._integer_column(np, self.params["group"], arrays[self.params["group"]])
        reference = arrays[self.params["reference"]].astype(np.float64)
        if not np.isfinite(reference).all() or (reference <= 0).any():
            raise ValueError(f"{self.params['reference']} must be finite and > 0")
        dg, dt = np.diff(group), np.diff(time)
        if ((dg < 0) | ((dg == 0) & (dt <= 0))).any():
            raise ValueError("rows must be strictly sorted by (group, time): unsorted or duplicate key found")
        return time, group, reference

    def _labels(self, np, arrays):
        """Return the ``(N, H)`` label matrix as given; NaN is allowed, inf is not."""
        labels = np.stack([arrays[name].astype(np.float64) for name in self._label_names()], axis=1)
        if np.isinf(labels).any():
            raise ValueError("label columns must not hold inf")
        return labels

    def _feature_names(self, arrays):
        """Return the feature column names in input order, refusing a stray label-named column."""
        p = self.params
        taken = {p["time"], p["group"], p["reference"], *self._label_names(), *p.get("drop", self.DEFAULT_DROP)}
        unknown = [d for d in p.get("drop", self.DEFAULT_DROP) if d not in arrays]
        if unknown:
            raise ValueError(f"{self.key}: drop names columns that do not exist: {unknown}")
        names = [name for name in arrays if name not in taken]
        stray = [n for n in names if n.startswith(p["label_prefix"])]
        if stray:
            raise ValueError(
                f"{self.key}: column(s) {stray} start with label_prefix {p['label_prefix']!r} but are not "
                f"a declared horizon; drop them or fix horizons (they would become features)")
        if not names:
            raise ValueError(f"{self.key}: no feature columns remain")
        return names

    def _check_features(self, np, arrays, names):
        """Refuse NaN outside ``nullable`` and inf anywhere in the float32 features."""
        nullable = set(self.params.get("nullable", self.DEFAULT_NULLABLE))
        unknown = sorted(nullable - set(names))
        if unknown:
            raise ValueError(f"{self.key}: nullable names are not feature columns: {unknown}")
        for name in names:
            column = arrays[name].astype(np.float32)
            bad = ~np.isfinite(column)
            if name in nullable:
                bad &= ~np.isnan(column)
            if bad.any():
                raise ValueError(f"feature {name!r} holds {int(bad.sum())} non-finite value(s) outside nullable")

    @staticmethod
    def _run_lengths(np, time, group, step_ms):
        """Return, per row, the count of consecutive ``step_ms`` bars of its group ending there."""
        contiguous = np.zeros(len(time), dtype=bool)
        contiguous[1:] = (np.diff(group) == 0) & (np.diff(time) == step_ms)
        index = np.arange(len(time))
        start = np.maximum.accumulate(np.where(contiguous, 0, index))
        return (index - start + 1).astype(np.int32)

    @staticmethod
    def _save(np, directory, key, array, fortran=False):
        """Write one array as a ``.npy`` file inside ``directory`` and return its path."""
        name, dtype = _FILES[key]
        path = os.path.join(directory, name)
        out = np.lib.format.open_memmap(path, mode="w+", dtype=dtype, shape=array.shape, fortran_order=fortran)
        out[...] = array
        out.flush()
        del out
        return path

    def _publish(self, np, arrays, names, time, group, reference, labels):
        """Build the directory beside ``path``, hash it, write the manifest and rename into place."""
        from dskit.pipeline.workflow import path_hash

        path = os.path.abspath(self.params["path"])
        pending = path + ".pending"
        if os.path.exists(path) or os.path.exists(pending):
            raise ValueError(f"{self.key}: panel path already exists: {path}")
        os.makedirs(pending)
        try:
            features = np.lib.format.open_memmap(
                os.path.join(pending, _FILES["features"][0]), mode="w+", dtype=_FILES["features"][1],
                shape=(len(time), len(names)), fortran_order=True)
            for j, name in enumerate(names):
                features[:, j] = arrays[name].astype(np.float32)
            features.flush()
            del features
            run = self._run_lengths(np, time, group, self.params["step_ms"])
            for key, array in (("labels", labels), ("time", time), ("group", group),
                               ("reference", reference), ("run", run)):
                self._save(np, pending, key, array)
            manifest = self._manifest(np, names, group, len(time), {
                spec[0]: path_hash(os.path.join(pending, spec[0])) for spec in _FILES.values()})
            atomic_write(os.path.join(pending, MANIFEST_NAME),
                         json.dumps(manifest, sort_keys=True, indent=1).encode("utf-8"))
            os.replace(pending, path)
        except BaseException:
            shutil.rmtree(pending, ignore_errors=True)
            raise
        return {"manifest": manifest, "path": path}

    def _manifest(self, np, names, group, n_rows, shas):
        """Return the manifest dict: layout, the knobs a reader needs, and per-file sha256."""
        p = self.params
        return {
            "format": FORMAT_VERSION,
            "n_rows": n_rows,
            "columns": names,
            "nullable": sorted(p.get("nullable", self.DEFAULT_NULLABLE)),
            "horizons": list(p["horizons"]),
            "label_prefix": p["label_prefix"],
            "step_ms": p["step_ms"],
            "time": p["time"],
            "group": p["group"],
            "reference": p["reference"],
            "groups": [int(g) for g in np.unique(group)],
            "dtypes": {spec[0]: spec[1] for spec in _FILES.values()},
            "unit_contract": UNIT_CONTRACT,
            "files": shas,
        }


class ArrayPanel:
    """A panel directory opened memory-mapped and read-only.

    Build one with :meth:`open` (a directory) or :meth:`from_source` (a registered store stream).
    Every array is ``np.load(mmap_mode='r')``, so many lanes share the page cache and none can
    write.

    Attributes
    ----------
    features, labels, time, group, reference, run : numpy.memmap
        The arrays described in the module docstring.
    columns : list of str
        Feature column names, in file order.
    groups : list of int
        The distinct group codes.
    step_ms : int
        The bar length.
    horizons : list of int
        The label horizons, in bars.
    manifest : dict
        The parsed manifest.

    Examples
    --------
    Open a panel and read its label span::

        panel = ArrayPanel.open("/store/panel")
        panel.label_span_ms   # -> max(horizons) * step_ms
    """

    def __init__(self, directory, manifest, arrays):
        self.directory = directory
        self.manifest = manifest
        self.features = arrays["features"]
        self.labels = arrays["labels"]
        self.time = arrays["time"]
        self.group = arrays["group"]
        self.reference = arrays["reference"]
        self.run = arrays["run"]
        self.columns = list(manifest["columns"])
        self.groups = list(manifest["groups"])
        self.step_ms = manifest["step_ms"]
        self.horizons = list(manifest["horizons"])

    @property
    def label_span_ms(self):
        """The longest label's reach in ms, ``max(horizons) * step_ms``; the one home of the purge length."""
        return max(self.horizons) * self.step_ms

    @classmethod
    def open(cls, directory, verify=False):
        """Open a panel directory read-only.

        Parameters
        ----------
        directory : str
            A directory written by :class:`ArrayPanelWrite`.
        verify : bool, optional
            Re-hash every file against the manifest first; default False.

        Returns
        -------
        ArrayPanel

        Raises
        ------
        ValueError
            A file is missing, fails its sha256, or has a shape the manifest contradicts.
        """
        import numpy as np
        from dskit.pipeline.workflow import path_hash

        directory = str(directory)
        with open(os.path.join(directory, MANIFEST_NAME), "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        arrays = {}
        for key, (name, _) in _FILES.items():
            path = os.path.join(directory, name)
            if not os.path.isfile(path):
                raise ValueError(f"panel {directory!r} is missing {name}")
            if verify and path_hash(path) != manifest["files"][name]:
                raise ValueError(f"panel file {name} fails its sha256 in the manifest")
            arrays[key] = np.load(path, mmap_mode="r")
        n = manifest["n_rows"]
        shapes = {"features": (n, len(manifest["columns"])), "labels": (n, len(manifest["horizons"]))}
        for key, array in arrays.items():
            if array.shape != shapes.get(key, (n,)):
                raise ValueError(f"panel file {_FILES[key][0]} has shape {array.shape}, manifest says {shapes.get(key, (n,))}")
        return cls(directory, manifest, arrays)

    @classmethod
    def from_source(cls, spec):
        """Open a panel registered as a ``localblobs`` stream, through the store.

        Parameters
        ----------
        spec : dict
            ``root`` (onboarding root), ``source``, ``stream`` and ``relpath`` (the panel directory
            inside the stream), all required; ``verify`` (bool, default False) re-hashes the whole
            snapshot first. Any other key is refused.

        Returns
        -------
        ArrayPanel

        Raises
        ------
        ValueError
            An unknown or missing key, a panel not in the stream, or a file whose store digest
            differs from the panel manifest.
        """
        from dskit.onboarding.artifacts import payload_files

        problems = []
        reject_unknown_params(problems, spec, _SOURCE_KEYS)
        problems += [f"{k} is required" for k in _SOURCE_KEYS[:4] if k not in spec]
        if problems:
            raise ValueError("; ".join(problems))
        got = payload_files(spec["root"], spec["source"], spec["stream"], verify=bool(spec.get("verify", False)))
        prefix = spec["relpath"].strip("/") + "/"
        if prefix + MANIFEST_NAME not in got["files"]:
            raise ValueError(
                f"no panel at {spec['relpath']!r} in stream {spec['stream']!r} of source {spec['source']!r}")
        directory = got["files"][prefix + MANIFEST_NAME].parent
        with open(got["files"][prefix + MANIFEST_NAME], "r", encoding="utf-8") as handle:
            files = json.load(handle)["files"]
        for name, digest in files.items():
            if got["sha256"].get(prefix + name) != digest:
                raise ValueError(f"store sha256 of {prefix + name} differs from the panel manifest")
        return cls.open(str(directory))

    def fingerprint(self):
        """Return the panel's identity: the manifest's hash and every file's sha256.

        Returns
        -------
        dict
            ``{"kind", "manifest", "files"}``; moves when any byte of the panel does.
        """
        from dskit.pipeline.workflow import path_hash

        return {"kind": type(self).__name__,
                "manifest": path_hash(os.path.join(self.directory, MANIFEST_NAME)),
                "files": dict(self.manifest["files"])}


class ColumnSelector:
    """Choose feature columns by name and prefix; an ablation arm is one of these.

    Start from ``names`` plus every column matching ``prefixes`` (or from all columns when neither
    is given), then remove ``except_names`` and the columns matching ``except_prefixes``. A key
    that is present must hold at least one entry: an empty list is refused, never read as "no
    selector", so a computed-empty ablation arm cannot silently become the full model.

    Parameters
    ----------
    spec : dict
        Any of ``names``, ``prefixes``, ``except_names``, ``except_prefixes`` (each a non-empty
        list of non-empty strings; omit a key rather than give it ``[]``). Other keys are refused.

    Examples
    --------
    Every sequence channel plus one named column::

        selector = ColumnSelector({"names": ["f0"], "prefixes": ["seq__"]})
        selector.select(panel.columns)   # -> indices in panel order
    """

    def __init__(self, spec):
        problems = []
        reject_unknown_params(problems, spec, _SELECTOR_KEYS)
        for knob in _SELECTOR_KEYS:
            problem = _str_list_problem(knob, spec[knob]) if knob in spec else None
            if problem is None and knob in spec and not spec[knob]:
                problem = f"{knob} is empty: omit the key instead (an empty list would select everything)"
            if problem:
                problems.append(problem)
        if problems:
            raise ValueError("; ".join(problems))
        self.spec = {k: list(v) for k, v in spec.items()}

    def select(self, columns):
        """Return the chosen column indices, in panel order.

        Parameters
        ----------
        columns : list of str
            The panel's feature column names.

        Returns
        -------
        list of int

        Raises
        ------
        ValueError
            A name or prefix that matches no column, or an empty result.
        """
        names = self.spec.get("names", [])
        prefixes = self.spec.get("prefixes", [])
        for knob in ("names", "except_names"):
            unknown = [n for n in self.spec.get(knob, []) if n not in columns]
            if unknown:
                raise ValueError(f"{knob} names unknown column(s) {unknown}")
        for knob in ("prefixes", "except_prefixes"):
            dead = [p for p in self.spec.get(knob, []) if not any(c.startswith(p) for c in columns)]
            if dead:
                raise ValueError(f"{knob} matches no column: {dead}")
        start = [c for c in columns if (not names and not prefixes) or c in names
                 or any(c.startswith(p) for p in prefixes)]
        drop_names, drop_prefixes = self.spec.get("except_names", []), tuple(self.spec.get("except_prefixes", []))
        kept = [c for c in start if c not in drop_names and not c.startswith(drop_prefixes)]
        if not kept:
            raise ValueError(f"selector {self.spec} leaves no column")
        return [i for i, c in enumerate(columns) if c in set(kept)]


class PanelBands:
    """The only place fit, calibration and validation rows are cut.

    Calibration is the last ``cal_days`` days inside the training period, ``[train_end - cal_days
    * DAY_MS + 1, train_end]``. A fit row is purged when its label reaches the calibration start
    (``label_reaches(t + span, cal_start)``), and the embargo between ``train_end`` and
    ``val_start`` must exceed the label span, so no band's label reaches the next band. A row is
    eligible only if all its labels are finite, ``run >= min_run``, and the bar ``max(horizons)``
    rows ahead is in its group and ``max(horizons)`` consecutive bars on (a forward gap would move
    a row-shifted label's real settle time past ``t + span``, past the purge).

    Parameters
    ----------
    panel : ArrayPanel
        The opened panel.
    assets : list of int
        Group codes to include; each must be in the panel.
    train_end_ms, val_start_ms, val_end_ms : int
        Epoch-ms cuts: last training instant, first and last validation instant (inclusive).
    cal_days : int
        Days of calibration, ``>= 1``.
    min_run : int
        Minimum consecutive bars ending at a row, ``>= 1``; set once for every cell.

    Examples
    --------
    Cut bands for one asset and read the fit rows::

        bands = PanelBands(panel, assets=[10], train_end_ms=te, val_start_ms=vs,
                           val_end_ms=ve, cal_days=30, min_run=1441)
        bands.fit_idx   # -> global row indices
    """

    def __init__(self, panel, assets, train_end_ms, val_start_ms, val_end_ms, cal_days, min_run):
        import numpy as np

        problems = []
        for name, value, floor in (("cal_days", cal_days, 1), ("min_run", min_run, 1)):
            check_int_param(problems, name, value, ge=floor)
        for name, value in (("train_end_ms", train_end_ms), ("val_start_ms", val_start_ms),
                            ("val_end_ms", val_end_ms)):
            check_int_param(problems, name, value, ge=0)
        if not isinstance(assets, (list, tuple)) or not assets:
            problems.append("assets must be a non-empty list of group codes")
        elif sorted(set(assets) - set(panel.groups)):
            problems.append(f"assets {sorted(set(assets) - set(panel.groups))} are not in the panel {panel.groups}")
        if problems:
            raise ValueError("; ".join(problems))
        span = panel.label_span_ms
        if val_start_ms - train_end_ms <= span:
            raise ValueError(
                f"embargo {val_start_ms - train_end_ms} ms is not longer than the label span {span} ms: "
                "the last training label would reach the validation band")
        if val_end_ms < val_start_ms:
            raise ValueError("val_end_ms is before val_start_ms")
        self.panel = panel
        self.assets = list(assets)
        self.train_end_ms, self.val_start_ms, self.val_end_ms = train_end_ms, val_start_ms, val_end_ms
        self.cal_days, self.min_run = cal_days, min_run
        self.cal_start_ms = train_end_ms - cal_days * DAY_MS + 1
        self._cut(np, span)

    @staticmethod
    def _label_bar_ahead(np, panel):
        """Return the mask of rows whose ``max(horizons)``-ahead bar is consecutive with them."""
        n, h = len(panel.time), max(panel.horizons)
        ahead = np.zeros(n, dtype=bool)
        if n > h:
            ahead[:n - h] = np.asarray(panel.run)[h:] >= h + 1
        return ahead

    def _eligible(self, np):
        """Return the mask of rows in the assets with finite labels, ``run >= min_run`` and a label bar ahead."""
        panel = self.panel
        mask = np.isin(panel.group, self.assets) & (np.asarray(panel.run) >= self.min_run)
        mask &= self._label_bar_ahead(np, panel)
        for start in range(0, len(mask), DEFAULT_CHUNK_ROWS):
            sl = slice(start, start + DEFAULT_CHUNK_ROWS)
            mask[sl] &= np.isfinite(panel.labels[sl]).all(axis=1)
        return mask

    def _cut(self, np, span):
        """Compute the three index arrays; an empty band refuses."""
        t = np.asarray(self.panel.time)
        eligible = self._eligible(np)
        masks = {
            "fit": eligible & ~label_reaches(t + span, self.cal_start_ms),
            "cal": eligible & (t >= self.cal_start_ms) & (t <= self.train_end_ms),
            "val": eligible & (t >= self.val_start_ms) & (t <= self.val_end_ms),
        }
        for name, mask in masks.items():
            rows = np.flatnonzero(mask)
            if not len(rows):
                raise ValueError(f"the {name} band is empty")
            setattr(self, f"{name}_idx", rows)

    def rows(self, band):
        """Return a band's global row indices, ascending.

        Parameters
        ----------
        band : str
            ``"fit"``, ``"cal"`` or ``"val"``.

        Returns
        -------
        numpy.ndarray
        """
        if band not in _BAND_NAMES:
            raise ValueError(f"band must be one of {_BAND_NAMES}, got {band!r}")
        return getattr(self, f"{band}_idx")

    def spans(self, band):
        """Return a band's per-group ``(lo, hi)`` half-open row ranges, for :meth:`DevicePanel.load`.

        Parameters
        ----------
        band : str
            ``"fit"``, ``"cal"`` or ``"val"``.

        Returns
        -------
        list of tuple
            One ``(first_row, last_row + 1)`` per group the band touches.
        """
        import numpy as np

        rows = self.rows(band)
        groups = np.asarray(self.panel.group)[rows]
        return [(int(rows[groups == g].min()), int(rows[groups == g].max()) + 1) for g in np.unique(groups)]

    def rows_sha256(self):
        """Return one sha256 over the panel identity, the cuts and the three index arrays.

        Returns
        -------
        str
            Equal for equal inputs, so training and scoring can assert they cut the same rows.
        """
        digest = hashlib.sha256()
        digest.update(json.dumps(self.panel.fingerprint(), sort_keys=True).encode("utf-8"))
        digest.update(json.dumps([self.assets, self.train_end_ms, self.val_start_ms, self.val_end_ms,
                                  self.cal_days, self.min_run]).encode("utf-8"))
        for band in _BAND_NAMES:
            digest.update(self.rows(band).astype("int64").tobytes())
        return digest.hexdigest()


class PanelScaler:
    """Per-column mean and standard deviation, fit chunk by chunk over the memmap.

    Build with :meth:`fit` on the fit band only. ``missing`` says what a NaN means: ``"refuse"``
    (default) raises at fit and at transform; ``"mean"`` ignores NaN when fitting and zero-fills
    them after scaling (no flag column is added).

    Attributes
    ----------
    columns : list of int
        The panel column indices the scaler was fit on.
    mean, std : numpy.ndarray
        float64 statistics, one per column; a constant column has ``std`` 1.
    missing : str
        The strategy.

    Examples
    --------
    Fit on the fit band and scale a block::

        scaler = PanelScaler.fit(panel, [0, 1], bands.fit_idx)
        scaled = scaler.transform(block, [0, 1])
    """

    def __init__(self, columns, mean, std, missing):
        self.columns, self.mean, self.std, self.missing = list(columns), mean, std, missing

    @classmethod
    def fit(cls, panel, columns, rows, missing=DEFAULT_MISSING, chunk_rows=DEFAULT_CHUNK_ROWS):
        """Fit mean and std of ``columns`` over ``rows``, ``chunk_rows`` at a time.

        Parameters
        ----------
        panel : ArrayPanel
            The opened panel.
        columns : list of int
            Panel feature column indices.
        rows : numpy.ndarray
            Global row indices to fit on (the fit band).
        missing : str, optional
            ``"refuse"`` (default) or ``"mean"``.
        chunk_rows : int, optional
            Rows per memmap read.

        Returns
        -------
        PanelScaler

        Raises
        ------
        ValueError
            An unknown strategy, a NaN under ``"refuse"``, or a column with no finite value.
        """
        import numpy as np

        if missing not in MISSING_STRATEGIES:
            raise ValueError(f"missing must be one of {MISSING_STRATEGIES}, got {missing!r}")
        width = len(columns)
        count, mean, m2 = np.zeros(width), np.zeros(width), np.zeros(width)
        rows = np.asarray(rows)
        for start in range(0, len(rows), chunk_rows):
            piece = rows[start:start + chunk_rows]
            block = _read_columns(panel, columns, piece).astype(np.float64)
            valid = ~np.isnan(block)
            if missing == "refuse" and not valid.all():
                raise ValueError("fit rows hold a missing (NaN) value and missing='refuse'")
            n = valid.sum(axis=0).astype(np.float64)
            safe = np.where(n > 0, n, 1.0)
            local_mean = np.where(valid, block, 0.0).sum(axis=0) / safe
            local_m2 = (np.where(valid, block - local_mean, 0.0) ** 2).sum(axis=0)
            total = count + n
            delta = local_mean - mean
            share = np.where(total > 0, n / np.where(total > 0, total, 1.0), 0.0)
            mean = mean + delta * share
            m2 = m2 + local_m2 + delta ** 2 * count * share
            count = total
        if (count == 0).any():
            raise ValueError("a fit column has no finite value in the fit rows")
        std = np.sqrt(m2 / count)
        return cls(columns, mean, np.where(std > 0, std, CONSTANT_COLUMN_STD), missing)

    def transform(self, block, columns):
        """Scale a ``(rows, len(columns))`` block with the fitted statistics.

        Parameters
        ----------
        block : numpy.ndarray
            Raw feature values for ``columns``.
        columns : list of int
            Which panel columns the block's columns are.

        Returns
        -------
        numpy.ndarray
            float32, ``(block - mean) / std``, with NaN zero-filled under ``"mean"``.

        Raises
        ------
        ValueError
            A column the scaler was not fit on, or a NaN under ``"refuse"``.
        """
        import numpy as np

        absent = [c for c in columns if c not in self.columns]
        if absent:
            raise ValueError(f"scaler was not fit on column(s) {absent}")
        pos = [self.columns.index(c) for c in columns]
        nan = np.isnan(block)
        if nan.any() and self.missing == "refuse":
            raise ValueError("block holds a missing (NaN) value and missing='refuse'")
        out = ((block - self.mean[pos]) / self.std[pos]).astype(np.float32)
        out[nan] = 0.0
        return out


class DevicePanel:
    """Selected panel columns resident on a device, gathered by global row index.

    Only the selected feature and channel columns are uploaded, in chunks straight from the memmap
    (no whole-matrix host copy), over each span extended back by ``(window - 1) * stride`` so the
    first window of a span is complete. A window is never stored as columns: ``gather`` builds
    ``(B, L, C)`` per batch. Only rows inside the caller's spans can be gathered (the back-extension
    is history, not a target), and only rows whose ``run`` covers the whole window. Build with
    :meth:`load`.

    Examples
    --------
    Load the fit band and gather a batch of 64-bar windows::

        dev = DevicePanel.load(panel, bands, [0, 1], [4, 5], bands.spans("fit"), scaler, "cuda",
                               window=64, stride=1)
        batch = dev.gather(torch.as_tensor(bands.fit_idx[:512]))
        batch.seq.shape   # -> (512, 64, 2)
    """

    def __init__(self):
        self.x_data = self.seq_data = self.y_data = self.ref_data = self.group_data = None
        self.run_data = None
        self._los = self._his = self._offs = self._tlos = self._this = None
        self.window = self.stride = 1
        self.device = "cpu"

    @classmethod
    def load(cls, panel, bands, features, channels, spans, scaler, device, window, stride):
        """Upload the selected columns over ``spans`` and return the resident panel.

        Parameters
        ----------
        panel : ArrayPanel
            The opened panel.
        bands : PanelBands
            The bands cut from ``panel``; its ``min_run`` is the one the window is held to, so a
            caller cannot state a different one.
        features : list of int
            Column indices of the flat per-row features ``x``.
        channels : list of int
            Column indices of the sequence channels (may be empty).
        spans : list of tuple
            Half-open global ``(lo, hi)`` row ranges whose rows will be gathered.
        scaler : PanelScaler
            Fit on at least ``features`` and ``channels``.
        device : str
            A torch device name.
        window, stride : int
            Window length ``L`` and bar stride, both ``>= 1``.

        Returns
        -------
        DevicePanel

        Raises
        ------
        ValueError
            Bands cut from another panel, a window longer than ``bands.min_run``
            (``(window - 1) * stride + 1`` bars), bad spans, or nothing selected.
        """
        import numpy as np
        import torch

        if bands.panel.directory != panel.directory:
            raise ValueError("bands were cut from another panel than the one being loaded")
        if window < 1 or stride < 1:
            raise ValueError("window and stride must be >= 1")
        need = (window - 1) * stride + 1
        if need > bands.min_run:
            raise ValueError(
                f"window needs {need} consecutive bars but bands.min_run is {bands.min_run}: rows would bridge a gap")
        if not features and not channels:
            raise ValueError("nothing selected: features and channels are both empty")
        merged = cls._merge(spans, (window - 1) * stride if channels else 0, len(panel.time))
        targets = cls._merge(spans, 0, len(panel.time))
        self = cls()
        self.window, self.stride, self.device = window, stride, device
        sizes = [hi - lo for lo, hi in merged]
        total = sum(sizes)
        self._los = torch.as_tensor([lo for lo, _ in merged], dtype=torch.long, device=device)
        self._his = torch.as_tensor([hi for _, hi in merged], dtype=torch.long, device=device)
        self._tlos = torch.as_tensor([lo for lo, _ in targets], dtype=torch.long, device=device)
        self._this = torch.as_tensor([hi for _, hi in targets], dtype=torch.long, device=device)
        self._offs = torch.as_tensor(np.concatenate([[0], np.cumsum(sizes)[:-1]]), dtype=torch.long, device=device)
        self.x_data = torch.empty((total, len(features)), dtype=torch.float32, device=device)
        self.seq_data = torch.empty((total, len(channels)), dtype=torch.float32, device=device) if channels else None
        self.y_data = torch.empty((total, len(panel.horizons)), dtype=torch.float32, device=device)
        self.ref_data = torch.empty(total, dtype=torch.float32, device=device)
        self.group_data = torch.empty(total, dtype=torch.long, device=device)
        self.run_data = torch.empty(total, dtype=torch.int32, device=device)
        offset = 0
        for lo, hi in merged:
            for start in range(lo, hi, DEFAULT_CHUNK_ROWS):
                sl = slice(start, min(hi, start + DEFAULT_CHUNK_ROWS))
                self._upload(panel, scaler, features, channels, sl, offset + start - lo)
            offset += hi - lo
        return self

    @staticmethod
    def _merge(spans, extend, total):
        """Extend each span back by ``extend`` rows, clip to the panel, and merge overlaps."""
        merged = []
        for lo, hi in sorted((max(0, lo - extend), hi) for lo, hi in spans):
            if not 0 <= lo < hi <= total:
                raise ValueError(f"span ({lo}, {hi}) is outside the panel's {total} rows")
            if merged and lo <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
            else:
                merged.append((lo, hi))
        if not merged:
            raise ValueError("spans must be non-empty")
        return merged

    def _upload(self, panel, scaler, features, channels, sl, at):
        """Copy one chunk of rows from the memmap onto the device at local offset ``at``."""
        import torch

        n = _row_count(sl, len(panel.time))
        end = at + n

        def put(target, array):
            target[at:end] = torch.from_numpy(array).to(self.device)

        if features:
            put(self.x_data, scaler.transform(_read_columns(panel, features, sl), features))
        if channels:
            put(self.seq_data, scaler.transform(_read_columns(panel, channels, sl), channels))
        put(self.y_data, panel.labels[sl].astype("float32"))
        put(self.ref_data, panel.reference[sl].astype("float32"))
        put(self.group_data, panel.group[sl].astype("int64"))
        put(self.run_data, panel.run[sl].astype("int32"))

    @staticmethod
    def _inside(los, his, idx):
        """Return ``(k, ok)``: the last range starting at or before each row, and whether it holds the row."""
        import torch

        k = (torch.searchsorted(los, idx, right=True) - 1).clamp(min=0)
        return k, (idx >= los[k]) & (idx < his[k])

    def _local(self, idx):
        """Map global row indices to local device offsets; a row outside the caller's spans refuses."""
        if self._los is None:
            raise ValueError("DevicePanel has been released")
        _, wanted = self._inside(self._tlos, self._this, idx)
        if not wanted.all().item():
            raise ValueError("gather row not loaded: it is outside every span the caller asked for "
                             "(the back-extension is history, not a target)")
        k, _ = self._inside(self._los, self._his, idx)
        return self._offs[k] + idx - self._los[k]

    def _refuse_gap_windows(self, local):
        """Refuse a target row whose ``run`` is shorter than the window's span."""
        need = (self.window - 1) * self.stride + 1
        short = self.run_data[local] < need
        if short.any().item():
            raise ValueError(f"gather row's window needs {need} consecutive bars but its run is shorter: "
                             "the window would bridge a gap")

    def gather(self, global_idx):
        """Gather one batch from global row indices.

        Parameters
        ----------
        global_idx : torch.Tensor or array-like
            Global row indices, all inside a span passed to :meth:`load`.

        Returns
        -------
        PanelBatch
            ``x`` ``(B, F)``; ``seq`` ``(B, L, C)`` chronological (oldest bar first, the target row
            last) or ``None`` without channels; ``y`` ``(B, H)``; ``ref`` ``(B,)``; ``group`` ``(B,)``.

        Raises
        ------
        ValueError
            A row outside the caller's spans, a row whose window would bridge a gap (its ``run`` is
            shorter than ``(window - 1) * stride + 1``), or a released panel.
        """
        import torch

        idx = torch.as_tensor(global_idx, dtype=torch.long, device=self.device)
        local = self._local(idx)
        seq = None
        if self.seq_data is not None:
            self._refuse_gap_windows(local)
            back = torch.arange(self.window - 1, -1, -1, device=self.device) * self.stride
            seq = self.seq_data[local[:, None] - back]
        return PanelBatch(self.x_data[local], seq, self.y_data[local],
                          self.ref_data[local], self.group_data[local])

    def release(self):
        """Free the device tensors; the panel cannot gather afterwards."""
        self.x_data = self.seq_data = self.y_data = self.ref_data = self.group_data = None
        self.run_data = None
        self._los = self._his = self._offs = self._tlos = self._this = None
        if str(self.device).startswith("cuda"):
            import torch

            torch.cuda.empty_cache()


#: Deliberately EMPTY: wired by import path
#: (``dskit.pipeline.libs.array_panel:ArrayPanelWrite``), nothing registers.
NODE_KINDS = ()
