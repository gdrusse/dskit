"""Deliberate input faults for a fitted predictive model (ADR-0316 Decision B1).

A *fault* turns a frame of forecast inputs into an altered copy, so a model that is already fitted can be
scored again on stale, missing or scaled features with no refit. Each fault is one subclass of
:class:`FeatureFault`; what is configurable is the class's params tuple, and construction is default-deny.
A fault never reads a label, never mutates its input and never judges the result: it only returns rows.

* :class:`StaleFeatures` - each row takes the declared columns from the row of the same identity
  ``sessions`` distinct dates earlier in the frame; rows without one leave.
* :class:`MissingFeatures` - the declared columns become missing (the caller's own rule fills them).
* :class:`ScaledFeatures` - the declared columns are multiplied by a factor.
* :class:`ForecastFaultStudy` - restores one completed pooled fit of a :class:`CDFExperiment`, scores its
  evaluation band again on each fault with the saved model and the medians saved at fit (never a refit), and
  writes statistics only: counts, shifts and a relative score change. It judges nothing; the child does.

Import cost: standard library plus ``dskit.pipeline`` modules; numpy and pandas are imported inside methods.
"""
import datetime
import importlib
import json
from abc import ABC, abstractmethod

from dskit.pipeline.base import ConfigError, value_hash
from dskit.pipeline.libs.cdf_experiment import AtomicFitStore, CDFExperiment, IntegrityError
from dskit.pipeline.records import number_ok

__all__ = ["FeatureFault", "StaleFeatures", "MissingFeatures", "ScaledFeatures", "ForecastFaultStudy",
           "REFUSAL_CODES", "MONOTONE_TOLERANCE"]

#: The only reasons a fault may be recorded as ``refused``; anything else aborts the study.
REFUSAL_CODES = ("infinite_input", "invalid_forecast", "no_paired_rows", "retained_below_minimum")
#: Slack of the monotone-CDF rule, restated from ``CDFExperiment.forecast`` (cdf_experiment.py, the loop that
#: raises "invalid component forecast probabilities"); a test pins the two texts together.
MONOTONE_TOLERANCE = 1e-12

#: Column holding the date a stale row is taken from while its source is joined.
_SOURCE = "__stale_source_date__"
#: Suffix that keeps a source row's feature columns apart from the target row's.
_SUFFIX = "__stale_source__"


class FeatureFault(ABC):
    """An abstract, default-deny alteration of the input features of a forecast frame.

    Parameters
    ----------
    **params
        Exactly the names in :meth:`params_tuple`. ``columns`` is a non-empty list of unique column names.

    Raises
    ------
    ConfigError
        Any unknown, missing or invalid param; every problem is listed in the one error.

    Examples
    --------
    Blank one feature, keeping every row::

        fault = MissingFeatures(columns=["f1"])
        altered = fault.apply(frame, ["symbol", "quote_date", "expiry"], "quote_date")
    """

    _BASE_PARAMS = ("columns",)
    _EXTRA_PARAMS = ()

    def __init__(self, **params):
        problems = self.validate_params(params)
        if problems:
            raise ConfigError([f"{type(self).__name__}: {p}" for p in problems])
        self.params = {k: (list(v) if isinstance(v, list) else v) for k, v in params.items()}

    @classmethod
    def params_tuple(cls):
        """Return every param name, ``_BASE_PARAMS`` then each class's ``_EXTRA_PARAMS`` up the bases."""
        names = []
        for owner in reversed(cls.__mro__):
            names.extend(owner.__dict__.get("_BASE_PARAMS", ()) + owner.__dict__.get("_EXTRA_PARAMS", ()))
        return tuple(dict.fromkeys(names))

    @classmethod
    def validate_params(cls, params):
        """Return every problem a params mapping has (empty when it is valid)."""
        if not isinstance(params, dict):
            return [f"params must be a mapping, got {type(params).__name__}"]
        allowed = cls.params_tuple()
        problems = []
        unknown = sorted(k for k in params if k not in allowed)
        if unknown:
            problems.append(f"unknown param(s) {unknown} - allowed: {list(allowed)}")
        for name in allowed:
            if name not in params:
                problems.append(f"{name} is required")
            else:
                problems.extend(getattr(cls, "_check_" + name)(params[name]))
        return problems

    @staticmethod
    def _check_columns(value):
        """Return the problems of a ``columns`` param: a non-empty list of unique non-empty names."""
        if (not isinstance(value, list) or not value
                or any(not isinstance(c, str) or not c for c in value) or len(set(value)) != len(value)):
            return ["columns must be a non-empty list of unique non-empty column names"]
        return []

    def feature_problems(self, features):
        """Return the problem (if any) of ``columns`` naming something outside the arm's ``features``."""
        outside = [c for c in self.params["columns"] if c not in features]
        return [f"columns {outside} are not features of the arm"] if outside else []

    def to_obj(self):
        """Return the canonical JSON object: the class path and every param, for hashing."""
        return {"class": type(self).__module__ + ":" + type(self).__name__,
                "params": {k: self.params[k] for k in self.params_tuple()}}

    def _check_frame(self, frame, identity, date):
        """Refuse a frame lacking a declared column, or a declared column that is part of the identity."""
        columns = self.params["columns"]
        missing = [c for c in columns if c not in frame.columns]
        if missing:
            raise ValueError(f"fault columns {missing} are not in the frame")
        keys = [c for c in columns if c in (*identity, date)]
        if keys:
            raise ValueError(f"fault columns {keys} are identity or date columns")

    @abstractmethod
    def apply(self, frame, identity, date):
        """Return the altered copy of ``frame``.

        Parameters
        ----------
        frame : pandas.DataFrame
            Forecast inputs with unique ``identity`` rows; never mutated.
        identity : list of str
            The identity columns, ``date`` among them.
        date : str
            The ISO-date column.

        Returns
        -------
        pandas.DataFrame
            Same columns; the index labels of the input name the rows kept (a fault may drop rows, never add).
        """


class MissingFeatures(FeatureFault):
    """Set the declared columns to missing on every row.

    Examples
    --------
    ::

        MissingFeatures(columns=["f1"]).apply(frame, identity, "quote_date")
    """

    def apply(self, frame, identity, date):
        """Return a copy whose declared columns are NaN, every row kept."""
        self._check_frame(frame, identity, date)
        out = frame.copy()
        out[self.params["columns"]] = float("nan")
        return out


class ScaledFeatures(FeatureFault):
    """Multiply the declared columns by a finite factor other than one.

    Examples
    --------
    ::

        ScaledFeatures(columns=["f1"], factor=2.0).apply(frame, identity, "quote_date")
    """

    _EXTRA_PARAMS = ("factor",)

    @staticmethod
    def _check_factor(value):
        """Return the problems of a ``factor``: a finite real number that is not 1."""
        if type(value) is bool or not number_ok(value) or value == 1:
            return ["factor must be a finite number other than 1"]
        return []

    def apply(self, frame, identity, date):
        """Return a copy whose declared columns are scaled, every row kept."""
        self._check_frame(frame, identity, date)
        out = frame.copy()
        out[self.params["columns"]] = out[self.params["columns"]] * self.params["factor"]
        return out


class StaleFeatures(FeatureFault):
    """Replace the declared columns by their values ``sessions`` distinct dates earlier.

    Sessions count over the frame's global sorted distinct dates, not per group. A row pairs with the row
    holding every identity key other than the date, on that earlier date; rows without one leave the frame.
    Values come only from strictly earlier dates.

    Examples
    --------
    One session back; a row on the first date, or without a same-identity row the day before, is dropped::

        StaleFeatures(columns=["f1"], sessions=1).apply(frame, identity, "quote_date")
    """

    _EXTRA_PARAMS = ("sessions",)

    @staticmethod
    def _check_sessions(value):
        """Return the problems of ``sessions``: a positive integer."""
        if type(value) is not int or value < 1:
            return ["sessions must be a positive integer"]
        return []

    def apply(self, frame, identity, date):
        """Return the kept rows with the declared columns taken from the earlier session."""
        self._check_frame(frame, identity, date)
        columns, sessions = self.params["columns"], self.params["sessions"]
        days = sorted(frame[date].unique())
        earlier = dict(zip(days[sessions:], days[:len(days) - sessions]))
        key = [c for c in identity if c != date]
        left = frame[frame[date].isin(earlier)].copy()
        left[_SOURCE] = left[date].map(earlier)
        left["__row__"] = range(len(left))
        source = frame[[*key, date, *columns]].rename(
            columns={date: _SOURCE, **{c: c + _SUFFIX for c in columns}})
        merged = left.merge(source, on=[*key, _SOURCE], how="inner", validate="many_to_one",
                            suffixes=("", _SUFFIX))
        merged = merged.sort_values("__row__")
        out = merged[list(frame.columns)].copy()
        for c in columns:
            out[c] = merged[c + _SUFFIX].to_numpy()
        out.index = left.index[merged["__row__"].to_numpy()]
        return out


class ForecastFaultStudy(CDFExperiment):
    """Score one fitted model's evaluation band again on deliberately altered inputs.

    The completed pooled fit of one fold, arm and candidate is restored under the identity ``run`` built; a
    missing fit refuses and nothing is ever fitted. The row set is that fit's evaluation band. The clean arm
    comes first and any failure there aborts the study; a failure after a fault is applied is recorded against
    that fault by a closed reason code. Only statistics are written: no forecast row, label or score level.

    Parameters
    ----------
    document : dict
        Exactly ``experiment`` (the fit document), ``fold``, ``arm``, ``candidate``, ``sealed_from`` (ISO
        date), ``grid`` (``z_grid``, ``weights``, ``tail``), ``faults`` (non-empty list of ``name``,
        ``class`` as ``module:Class``, ``params``; names unique), ``min_retained`` (fraction in (0, 1]),
        ``min_scored`` (positive integer) and ``output`` (a fresh directory).

    Raises
    ------
    ConfigError
        Every problem the document has, listed at once.
    IntegrityError
        The experiment reads at or past ``sealed_from``, or provenance does not hold.

    Examples
    --------
    ::

        study = ForecastFaultStudy(document)
        statistics = study.assess()
        statistics["faults"][0]["cdf_shift_mean"]["value"]
    """

    DOCUMENT_KEYS = ("experiment", "fold", "arm", "candidate", "sealed_from", "grid", "faults",
                     "min_retained", "min_scored", "output")
    GRID_KEYS = ("z_grid", "weights", "tail")
    FAULT_KEYS = ("name", "class", "params")

    def __init__(self, document):
        if not isinstance(document, dict) or not isinstance(document.get("experiment"), dict):
            raise ConfigError(["document must be a mapping with an experiment mapping"])
        super().__init__(document["experiment"])
        problems = self._document_problems(document)
        if problems:
            raise ConfigError(problems)
        self.document = json.loads(json.dumps(document))
        self.faults = [self._fault_class(f["class"])(**f["params"]) for f in document["faults"]]
        self.study_identity = value_hash(self.document)
        self._seal_experiment()

    @staticmethod
    def _fault_class(path):
        """Return the FeatureFault subclass a ``module:Class`` path names, or None."""
        if not isinstance(path, str) or path.count(":") != 1:
            return None
        module, name = path.split(":", 1)
        try:
            cls = getattr(importlib.import_module(module), name)
        except (ImportError, AttributeError, ValueError):
            return None
        ok = isinstance(cls, type) and issubclass(cls, FeatureFault) and cls is not FeatureFault
        return cls if ok else None

    def _document_problems(self, document):
        """Return every problem the study keys, scalar settings and grid have."""
        extra = sorted(set(document) - set(self.DOCUMENT_KEYS))
        absent = [k for k in self.DOCUMENT_KEYS if k not in document]
        if extra or absent:
            return [f"document keys must be exactly {list(self.DOCUMENT_KEYS)}; unknown {extra}, missing {absent}"]
        problems = []
        fold = document["fold"]
        if not any(type(known) is type(fold) and known == fold for known in self.study.plan.ids()):
            problems.append("fold is not a fold of the experiment")
        if not isinstance(document["arm"], str) or document["arm"] not in self.config["arms"]:
            problems.append("arm is not an arm of the experiment")
        if document["candidate"] not in [c["name"] for c in self.config["candidates"]]:  # a list compares, never hashes
            problems.append("candidate is not a candidate of the experiment")
        problems.extend(self._date_problems(document["sealed_from"]))
        problems.extend(self._grid_problems(document["grid"]))
        retained, scored = document["min_retained"], document["min_scored"]
        if type(retained) is bool or not number_ok(retained) or not 0 < retained <= 1:
            problems.append("min_retained must be a number in (0, 1]")
        if type(scored) is not int or scored < 1:
            problems.append("min_scored must be a positive integer")
        if not isinstance(document["output"], str) or not document["output"]:
            problems.append("output must be a non-empty path")
        elif self._inside_experiment_output(document["output"]):
            problems.append("output must not equal or lie inside the experiment's output directory")
        problems.extend(self._fault_problems(document))
        return problems

    def _inside_experiment_output(self, path):
        """Say whether ``path`` is the experiment's output directory or lies inside it (resolved paths)."""
        from pathlib import Path

        fitted, study = Path(self.config["output"]).resolve(), Path(path).resolve()
        return study == fitted or fitted in study.parents

    @staticmethod
    def _date_problems(value):
        """Return the problem of ``sealed_from`` not being an ISO calendar date."""
        try:
            if datetime.date.fromisoformat(value).isoformat() == value:
                return []
        except (TypeError, ValueError):
            pass
        return ["sealed_from must be an ISO date YYYY-MM-DD"]

    def _grid_problems(self, grid):
        """Return the problems of the CDF grid, the threshold weights and the tail width."""
        if not isinstance(grid, dict) or set(grid) != set(self.GRID_KEYS):
            return [f"grid keys must be exactly {list(self.GRID_KEYS)}"]
        z, weights, tail = grid["z_grid"], grid["weights"], grid["tail"]

        def numbers(v):
            return isinstance(v, list) and all(type(x) is not bool and number_ok(x) for x in v)
        problems = []
        if not numbers(z) or len(z) < 2 or any(b <= a for a, b in zip(z, z[1:])):
            problems.append("grid.z_grid must be at least two strictly increasing numbers")
        if (not numbers(weights) or (numbers(z) and len(weights) != len(z))
                or any(w < 0 for w in weights) or not any(weights)):
            problems.append("grid.weights must be one non-negative number per z_grid point, not all zero")
        if type(tail) is bool or not number_ok(tail) or tail <= 0:
            problems.append("grid.tail must be a positive number")
        return problems

    def _fault_problems(self, document):
        """Return the problems of every fault entry: shape, unique name, class, params, columns."""
        faults = document["faults"]
        if not isinstance(faults, list) or not faults:
            return ["faults must be a non-empty list"]
        problems, names = [], []
        arm = self.config["arms"].get(document["arm"]) if isinstance(document["arm"], str) else None
        study = self.config["study"]
        for number, entry in enumerate(faults):
            where = f"faults[{number}]"
            if not isinstance(entry, dict) or set(entry) != set(self.FAULT_KEYS):
                problems.append(f"{where} keys must be exactly {list(self.FAULT_KEYS)}")
                continue
            name = entry["name"]
            if not isinstance(name, str) or not name or "/" in name or "\\" in name or name in (".", ".."):
                problems.append(f"{where}.name must be a safe non-empty string")
            if isinstance(name, str):
                names.append(name)
            cls = self._fault_class(entry["class"])
            if cls is None:
                problems.append(f"{where}.class must name a FeatureFault subclass as module:Class")
                continue
            found = cls.validate_params(entry["params"])
            problems.extend(f"{where}.params: {p}" for p in found)
            if found or arm is None:
                continue
            fault = cls(**entry["params"])
            problems.extend(f"{where}: {p}" for p in fault.feature_problems(arm["features"]))
            keys = [c for c in fault.params["columns"] if c in (*study["identity"], study["date"])]
            if keys:
                problems.append(f"{where}: columns {keys} are identity or date columns")
        if len(set(names)) != len(names):
            problems.append("fault names must be unique")
        return problems

    def _seal_experiment(self):
        """Refuse an experiment whose read bounds pass the owner's seal."""
        sealed = self.document["sealed_from"]
        if self.config["date_upper_exclusive"] > sealed or self.config["end_upper_exclusive"] > sealed:
            raise IntegrityError("experiment read bounds pass sealed_from")

    def _seal_rows(self, band):
        """Refuse an evaluation row whose date or label end is at or after the seal."""
        s, sealed = self.config["study"], self.document["sealed_from"]
        if (band[s["date"]] >= sealed).any() or (band[s["end"]] >= sealed).any():
            raise IntegrityError("evaluation rows reach sealed_from")

    def _restore(self, bands, groups):
        """Verify and return the completed pooled fit of the declared fold, arm and candidate."""
        d, keys = self.document, self.config["study"]["identity"]
        identity = {"experiment": self.identity, "regime": "pooled", "arm": d["arm"], "model": d["candidate"],
                    "fold": d["fold"], "groups": groups,
                    "band_identities": [value_hash(b[keys].values.tolist()) for b in bands]}
        root = self.output/"pooled"/d["arm"]/d["candidate"]/str(d["fold"])/"pooled"
        if not (root/"fit").exists() or not (root/"completed").exists():
            raise IntegrityError("no completed pooled fit to restore; this study never fits")
        record = AtomicFitStore(root/"fit", identity).verify()
        AtomicFitStore(root/"completed", identity).verify()
        summary = json.loads((root/"completed/summary.json").read_text())
        metadata = json.loads((root/"fit/metadata.json").read_text())
        if (summary["checkpoint"] != value_hash(record) or metadata["groups"] != groups
                or metadata["features"] != self.config["arms"][d["arm"]]["features"]):
            raise IntegrityError("fit metadata or identity mismatch")
        return root, record, metadata

    @staticmethod
    def _valid_cdf(values):
        """Say whether CDF rows are finite, inside [0, 1] and monotone (restated from ``forecast``)."""
        import numpy as np

        return bool(np.isfinite(values).all() and not (values < 0).any() and not (values > 1).any()
                    and not (np.diff(values, axis=1) < -MONOTONE_TOLERANCE).any())

    def _cdf(self, model, x):
        """Return the model's clipped CDF on the declared grid, one row per input row."""
        import numpy as np

        z = np.asarray(self.document["grid"]["z_grid"], float)
        return np.clip(model.curve(x).cdf(z), 0., 1.)

    def _inputs(self, frame, groups, medians):
        """Return the model input matrix of a frame, imputed with the medians saved at fit."""
        arm = self.config["arms"][self.document["arm"]]
        return self._arrays((frame,), arm, groups, "pooled", medians)[0][0]

    def _extent(self, frame, rows):
        """Return the distinct date and group counts of the selected rows."""
        s = self.config["study"]
        chosen = frame.iloc[rows]
        return {"dates": int(chosen[s["date"]].nunique()), "groups": int(chosen[s["group"]].nunique())}

    def _pair(self, band, faulted):
        """Return the positions in ``band`` and in ``faulted`` of the rows present in both, by identity."""
        import numpy as np

        keys = self.config["study"]["identity"]
        right = faulted[keys].reset_index(drop=True)
        right["__position__"] = np.arange(len(right))
        if right[keys].duplicated().any():
            raise IntegrityError("a fault repeated an identity")
        merged = band[keys].reset_index(drop=True).merge(right, on=keys, how="left")
        found = merged["__position__"].notna().to_numpy()
        if int(found.sum()) != len(right):
            raise IntegrityError("a fault produced rows outside the evaluation band")
        return np.flatnonzero(found), merged["__position__"].to_numpy()[found].astype(int)

    @staticmethod
    def _count(value, n, extent):
        """Return one statistic: its value, the rows behind it, and their distinct dates and groups."""
        return {"value": None if value is None else float(value), "n": int(n), **extent}

    def _refused(self, name, reason, rows, paired, retained):
        """Return the record of a fault that stopped with a closed reason code."""
        none = self._count(None, 0, {"dates": 0, "groups": 0})
        return {"name": name, "status": "refused", "reason": reason, "rows": rows, "paired_rows": paired,
                "retained_fraction": retained, "changed_rows": None, "cdf_shift_mean": none,
                "cdf_shift_max": none, "score_change": none}

    def _score_change(self, band, clean, faulted, rows):
        """Return the relative change of the mean grid score on the paired rows both arms can score."""
        import numpy as np
        from dskit.pipeline.libs.cdf_grid_methods import GridCDFMethod

        s, grid = self.config["study"], self.document["grid"]
        y = (band[s["target"]]/band[s["reference"]]).to_numpy(float)[rows]
        before, after = [GridCDFMethod.grid_score_rows(cdf, y, grid["z_grid"], grid["weights"], grid["tail"])
                         for cdf in (clean, faulted)]
        keep = np.isfinite(before) & np.isfinite(after)
        n = int(keep.sum())
        mean = before[keep].mean() if n else 0.
        value = after[keep].mean()/mean - 1. if n >= self.document["min_scored"] and mean > 0 else None
        return self._count(value, n, self._extent(band, rows[keep]))

    def _assess_fault(self, fault, name, band, clean, run):
        """Return the statistics of one fault against the clean arm, or its closed refusal."""
        import numpy as np

        s, arm = self.config["study"], self.config["arms"][self.document["arm"]]
        total = len(band)
        faulted = fault.apply(band, s["identity"], s["date"])
        if np.isinf(faulted[arm["features"]].to_numpy(float)).any():
            return self._refused(name, "infinite_input", total, None, None)
        left, right = self._pair(band, faulted)
        paired, retained = len(left), len(left)/total
        if not paired:
            return self._refused(name, "no_paired_rows", total, 0, 0.)
        if retained < self.document["min_retained"]:
            return self._refused(name, "retained_below_minimum", total, paired, retained)
        x_fault = self._inputs(faulted, run["groups"], run["medians"])[right]
        if not np.isfinite(x_fault).all():      # finite as float64, infinite once cast to the model's float32
            return self._refused(name, "infinite_input", total, paired, retained)
        cdf_fault = self._cdf(run["model"], x_fault)
        if not self._valid_cdf(cdf_fault):
            return self._refused(name, "invalid_forecast", total, paired, retained)
        shift = np.abs(cdf_fault - clean["cdf"][left])
        changed = int((clean["x"][left] != x_fault).any(axis=1).sum())
        extent = self._extent(band, left)
        return {"name": name, "status": "ran" if changed and shift.max() > 0 else "inert",
                "rows": total, "paired_rows": paired, "retained_fraction": retained, "changed_rows": changed,
                "cdf_shift_mean": self._count(shift.mean(), paired, extent),
                "cdf_shift_max": self._count(shift.max(), paired, extent),
                "score_change": self._score_change(band, clean["cdf"][left], cdf_fault, left)}

    def _clean_arm(self, band, run):
        """Return the clean arm's inputs and CDF; any failure aborts the study."""
        x = self._inputs(band, run["groups"], run["medians"])
        cdf = self._cdf(run["model"], x)
        if not self._valid_cdf(cdf):
            raise IntegrityError("invalid clean forecast probabilities")
        return {"x": x, "cdf": cdf}

    def _evaluation(self):
        """Return the restored fit and the sealed evaluation band of the declared fold and arm."""
        frame = self.load_panel()
        d = self.document
        arm = self.config["arms"][d["arm"]]
        bands, groups, _ = self._bands(frame, self.study.plan.calendar(frame), d["fold"], arm)
        if not groups or bands[2].empty:
            raise IntegrityError("no admitted groups or no evaluation rows")
        self._seal_rows(bands[2])
        return bands[2], groups, self._restore(bands, groups)

    def _statistics(self, band, run):
        """Return the statistics document: the clean extent and one record per fault, in order."""
        clean = self._clean_arm(band, run)
        records = [self._assess_fault(fault, entry["name"], band, clean, run)
                   for fault, entry in zip(self.faults, self.document["faults"])]
        return {"clean": {"rows": len(band), **self._extent(band, slice(None))}, "faults": records}

    def run(self, regime, worker=0, workers=1, limit=None):
        """Refuse: a fault study restores a completed fit and never fits."""
        raise IntegrityError("a fault study never fits; fit with CDFExperiment.run")

    def forecast(self, spec):
        """Refuse: a fault study writes statistics only, never forecast rows."""
        raise IntegrityError("a fault study writes no forecast rows; use assess")

    def assess(self):
        """Run the study once and publish ``statistics.json`` and ``receipt.json`` atomically.

        Returns
        -------
        dict
            The statistics: ``clean`` extent and one record per fault, in document order. A repeat call on a
            published, verified output returns the stored statistics.

        Raises
        ------
        IntegrityError
            Provenance, the seal, the restored fit or the clean arm is invalid, or a fault failed in a way
            the closed refusal codes do not cover.
        """
        import fcntl
        from dskit.pipeline.libs.predictive_cdf import TorchCDF

        self._verify_provenance()
        band, groups, (root, record, metadata) = self._evaluation()
        identity = {"study": self.study_identity, "checkpoint": value_hash(record)}
        store = AtomicFitStore(self.document["output"], identity)
        store.path.parent.mkdir(parents=True, exist_ok=True)
        with (store.path.parent/(store.path.name+".lock")).open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if store.path.exists():
                store.verify()
                return json.loads((store.path/"statistics.json").read_text())
            run = {"model": TorchCDF.load_checkpoint(root/"fit/model"), "groups": groups,
                   "medians": metadata["medians"]}
            statistics = self._statistics(band, run)
            keys = self.config["study"]["identity"]
            receipt = {"identity": identity, "experiment": self.identity, "groups": groups,
                       "faults": [f["name"] for f in self.document["faults"]],
                       "evaluation_identity": value_hash(band[keys].values.tolist()),
                       "rows": len(band), "scores_written": False}
            store.publish({"statistics.json": self.json_bytes(statistics),
                           "receipt.json": self.json_bytes(receipt)})
            return statistics
