"""Deliberate input faults for a fitted predictive model (ADR-0316 Decision B1).

A *fault* turns a frame of forecast inputs into an altered copy, so a model that is already fitted can be
scored again on stale, missing or scaled features with no refit. Each fault is one subclass of
:class:`FeatureFault`; what is configurable is the class's params tuple, and construction is default-deny.
A fault never reads a label, never mutates its input and never judges the result: it only returns rows.

* :class:`StaleFeatures` - each row takes the declared columns from the row of the same identity
  ``sessions`` distinct dates earlier in the frame; rows without one leave.
* :class:`MissingFeatures` - the declared columns become missing (the caller's own rule fills them).
* :class:`ScaledFeatures` - the declared columns are multiplied by a factor.

Import cost: standard library plus ``dskit.pipeline.base`` and ``records``; pandas is imported inside methods.
"""
from abc import ABC, abstractmethod

from dskit.pipeline.base import ConfigError
from dskit.pipeline.records import number_ok

__all__ = ["FeatureFault", "StaleFeatures", "MissingFeatures", "ScaledFeatures"]

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
