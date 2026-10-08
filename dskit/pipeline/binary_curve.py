"""Price a binary contract from any CDF curve, through the existing payoff geometries.

:mod:`dskit.pipeline.binary_pricing` prices a contract from a survival function
``s(K) = P(A >= K)`` that one closed-form law supplies. Any other source of a distribution — an
option-implied curve, a fitted predictive CDF, an empirical one — arrives as KNOTS: a monotone
CDF sampled at increasing abscissae. This module turns those knots into the same survival
callable and hands it to the same geometries (:data:`~dskit.pipeline.binary_pricing.PAYOFFS`),
so a curve prices a binary without a second payoff vocabulary.

The axis. Knots are on one of two axes, named by ``axis``. ``"level"``: the abscissa is the
settlement value itself. ``"log_return"``: the abscissa is ``log(level / reference)``, the shape
``libs.predictive_cdf.OptionPriceCDF.estimate`` emits (``log_returns`` against ``probabilities``,
measured from the spot it was given, whatever ``forward_ratio`` says), so the reference is that
spot. A strike ``K`` is read at ``log(K / reference)``.

Interpolation and support. Between knots the CDF is linear in the abscissa (monotone by
construction, the rule ``GridCurve`` uses). Outside the first and last knot nothing is known, so
a strike there is REFUSED (:meth:`CurveSurvival.covers`; the node marks the row
``outside_support``), never extrapolated: a curve whose support ends short of the strike says
nothing about the tail beyond it, and clamping to 0 or 1 would invent a certainty.

Atoms. Repeated abscissae are a jump in the CDF (a point mass). Survival is ``P(S >= K)``, so it
reads the CDF from the LEFT at ``K``: at a jump the mass at ``K`` counts toward survival, the
convention of the ``above`` geometry (``A >= lower``). At any other point the left and right
limits agree.

Import cost: stdlib only.
"""

import math
import numbers
import operator
from bisect import bisect_left
from collections import Counter

from dskit.pipeline.binary_pricing import PAYOFFS, STATUS_SUFFIX
from dskit.pipeline.node import Node, reject_unknown_params
from dskit.pipeline.records import number_ok, price_ok

__all__ = [
    "AXES",
    "AXIS_LEVEL",
    "AXIS_LOG_RETURN",
    "KEY_MISSING",
    "STATUSES",
    "STATUS_OK",
    "STATUS_SUFFIX",
    "CurveBinaryFairValue",
    "CurveSurvival",
    "fields_key",
    "key_fields_problems",
    "row_key",
]

#: The problem :func:`row_key` reports for a cell that names nothing: ``None``, absent, or ``""``.
KEY_MISSING = "missing"

#: The two abscissa conventions a curve's knots may be on.
AXIS_LEVEL = "level"
AXIS_LOG_RETURN = "log_return"
AXES = (AXIS_LEVEL, AXIS_LOG_RETURN)

#: The status a priced row carries; every other status names why it was not priced.
STATUS_OK = "ok"
#: Every status the node writes, in the order its checks run.
STATUSES = (STATUS_OK, "bad_curve_key", "no_curve", "ineligible_curve", "bad_curve", "unknown_payoff",
            "bad_bounds", "outside_support")


class CurveSurvival:
    """The survival function ``s(K) = P(S >= K)`` of a CDF given by knots.

    Parameters
    ----------
    values : sequence of float
        Abscissae, nondecreasing, at least two, finite (a tie is an atom).
    probabilities : sequence of float
        CDF values at ``values``, nondecreasing, inside ``[0, 1]``, one per value.
    axis : str
        ``"level"`` (default) or ``"log_return"`` (see the module docstring).
    reference : float or None
        The level ``log_return`` abscissae are measured from, positive; required for that axis
        and refused for ``"level"``.

    Raises
    ------
    ValueError
        When :meth:`problems` names any problem.

    Examples
    --------
    A curve from ``OptionPriceCDF``'s output, read at a strike of 105 against a spot of 100::

        curve = CurveSurvival(out["log_returns"], out["probabilities"], axis="log_return",
                              reference=100.0)
        curve.survival(105.0) if curve.covers(105.0) else None
    """

    def __init__(self, values, probabilities, axis=AXIS_LEVEL, reference=None):
        problems = self.problems(values, probabilities, axis, reference)
        if problems:
            raise ValueError("; ".join(problems))
        self._values = [float(v) for v in values]
        self._probabilities = [float(p) for p in probabilities]
        self._axis, self._reference = axis, reference

    @staticmethod
    def problems(values, probabilities, axis=AXIS_LEVEL, reference=None):
        """List what makes the knots unusable, empty when none.

        Parameters
        ----------
        values, probabilities, axis, reference
            As for the constructor.

        Returns
        -------
        list of str
            Every problem found.
        """
        problems = []
        if axis not in AXES:
            problems.append(f"axis must be one of {AXES}, got {axis!r}")
        elif axis == AXIS_LOG_RETURN and not price_ok(reference):
            problems.append(f"a log_return axis needs a positive finite reference level, got {reference!r}")
        elif axis == AXIS_LEVEL and reference is not None:
            problems.append(f"a level axis takes no reference, got {reference!r}")
        if not (isinstance(values, (list, tuple)) and isinstance(probabilities, (list, tuple))):
            return problems + ["values and probabilities must be lists"]
        if len(values) < 2 or len(values) != len(probabilities):
            return problems + [f"need at least two knots and one probability per value, got {len(values)} "
                               f"values and {len(probabilities)} probabilities"]
        if not all(number_ok(v) for v in values) or not all(number_ok(p) for p in probabilities):
            return problems + ["every knot must be a finite number"]
        if any(b < a for a, b in zip(values, values[1:])):
            problems.append("values must be nondecreasing")
        if any(b < a for a, b in zip(probabilities, probabilities[1:])):
            problems.append("probabilities must be nondecreasing")
        if probabilities[0] < 0.0 or probabilities[-1] > 1.0:
            problems.append("probabilities must lie inside [0, 1]")
        return problems

    def _abscissa(self, level):
        """Return the knot-axis coordinate of ``level``."""
        if self._axis == AXIS_LEVEL:
            return float(level)
        return math.log(level / self._reference)

    def covers(self, level):
        """Say whether ``level`` lies inside the knots' support (endpoints included).

        Parameters
        ----------
        level : float
            A settlement level, positive for a ``log_return`` axis.

        Returns
        -------
        bool
            False outside the support, or when ``level`` is unusable on this axis.
        """
        if not (price_ok(level) if self._axis == AXIS_LOG_RETURN else number_ok(level)):
            return False
        x = self._abscissa(level)
        return self._values[0] <= x <= self._values[-1]

    def survival(self, level):
        """Return ``P(S >= level)``, the CDF read from the left at ``level``.

        Parameters
        ----------
        level : float
            A settlement level inside the support.

        Returns
        -------
        float
            The survival probability.

        Raises
        ------
        ValueError
            When ``level`` is outside the support (never extrapolated).
        """
        if not self.covers(level):
            raise ValueError(f"level {level!r} is outside the curve's support "
                             f"[{self._values[0]!r}, {self._values[-1]!r}] on its {self._axis} axis")
        x, xs, ps = self._abscissa(level), self._values, self._probabilities
        i = bisect_left(xs, x)          # the first knot >= x: the left limit at x
        if i == 0:
            return 1.0 - ps[0]
        left, right = xs[i - 1], xs[i]
        cdf = ps[i - 1] + (ps[i] - ps[i - 1]) * (x - left) / (right - left)
        return 1.0 - cdf

    __call__ = survival


class CurveBinaryFairValue(Node):
    """Add the fair value of YES to each contract row from the CDF curve it names (role ``transform``).

    A sibling of :class:`~dskit.pipeline.binary_pricing.BinaryFairValue`: the same geometries
    and the same column trio, with the survival function read off a curve instead of a law.
    Inputs: ``records``, the contract rows (payoff name, bounds, the curve key); and ``curves``,
    one row per curve with its id, knots and (for a ``log_return`` axis) its reference level.
    Keys are matched column by column through :func:`row_key` (str and non-bool int scalars; ``1``
    and ``"1"`` differ): a contract whose key has a missing cell (None, absent or ``""``) is
    ``no_curve``, one with a refused cell ``bad_curve_key``; a curve row whose id has a missing cell
    is never read, and a refused cell or a repeated id is an input problem.
    Outputs: ``records``, every row with ``<fair_field>`` (P(YES), or None) and
    ``<fair_field>_status`` (one of :data:`STATUSES`); and ``census``,
    ``{"rows", "priced", "by_status"}``.

    Parameters
    ----------
    params : dict
        REQUIRED: ``payoff_field``, ``lower_field``, ``upper_field``, ``curve_key_fields`` (a
        list of columns on a contract row), ``curve_id_fields`` (the same number of columns on a
        curve row, matched in order), ``values_field``, ``probabilities_field`` (on a curve row),
        ``axis`` (one of :data:`AXES`) and ``fair_field`` (the output column).
        ``reference_field`` (the curve row's reference level) is required for a ``log_return``
        axis and refused for ``level``. OPTIONAL ``eligible_field``: a curve row whose value
        there is not ``True`` prices nothing (``ineligible_curve``).

    Examples
    --------
    Price contracts from option-implied curves, one per snapshot::

        node = CurveBinaryFairValue("fair", {
            "payoff_field": "payoff", "lower_field": "lo", "upper_field": "hi",
            "curve_key_fields": ["snapshot"], "curve_id_fields": ["snapshot"],
            "values_field": "log_returns", "probabilities_field": "probabilities",
            "axis": "log_return", "reference_field": "spot", "eligible_field": "eligible",
            "fair_field": "fair"})
        out = node.run(ctx, {"records": contracts, "curves": curves})
        # -> out["records"][0]["fair"] is P(YES), or None with fair_status saying why
    """

    role = "transform"
    outputs = ("records", "census")
    _CONTRACT_FIELDS = ("payoff_field", "lower_field", "upper_field")
    _CURVE_FIELDS = ("values_field", "probabilities_field")
    _KEY_PARAMS = ("curve_key_fields", "curve_id_fields")
    _PARAMS = (*_CONTRACT_FIELDS, *_KEY_PARAMS, *_CURVE_FIELDS, "axis", "reference_field", "eligible_field",
               "fair_field")

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
        for name in (*cls._CONTRACT_FIELDS, *cls._CURVE_FIELDS, "fair_field"):
            if not _name_ok(params.get(name)):
                problems.append(f"{name} is required: a non-empty column name, got {params.get(name)!r}")
        problems += key_fields_problems(params, *cls._KEY_PARAMS)
        axis = params.get("axis")
        if axis not in AXES:
            problems.append(f"axis is required: one of {AXES}, got {axis!r}")
        elif axis == AXIS_LOG_RETURN and not _name_ok(params.get("reference_field")):
            problems.append(f"reference_field is required for a log_return axis: a non-empty column name, "
                            f"got {params.get('reference_field')!r}")
        elif axis == AXIS_LEVEL and "reference_field" in params:
            problems.append("reference_field is refused for a level axis: its knots are levels already")
        if "eligible_field" in params and not _name_ok(params["eligible_field"]):
            problems.append(f"eligible_field must be a non-empty column name, got {params['eligible_field']!r}")
        return problems + cls._collision_problems(params)

    @classmethod
    def _collision_problems(cls, params):
        """Problems with an output column that would overwrite a contract input column."""
        name = params.get("fair_field")
        if not _name_ok(name):
            return []
        outputs = {name, name + STATUS_SUFFIX}
        named = {params.get(k) for k in cls._CONTRACT_FIELDS if _name_ok(params.get(k))}
        keys = params.get("curve_key_fields")
        named |= {f for f in keys if _name_ok(f)} if isinstance(keys, list) else set()
        clash = sorted(outputs & named)
        return [f"fair_field {name!r} writes {sorted(outputs)}, which would overwrite the input column(s) {clash}"
                ] if clash else []

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Answer ``pure``: the columns are a function of the rows and the params.

        Parameters
        ----------
        params : dict
            Unused.
        verified_run_evidence : dict
            Unused.

        Returns
        -------
        str
            ``"pure"``.
        """
        return "pure"

    def validate_inputs(self, inputs):
        """Refuse a port that is not a list, and a curve id listed twice.

        Parameters
        ----------
        inputs : dict
            The wired ports.

        Returns
        -------
        list of str
            One problem per unusable port or duplicated curve id.
        """
        problems = [f"{port} must be a list of rows, got {type(inputs.get(port)).__name__}"
                    for port in ("records", "curves") if not isinstance(inputs.get(port), list)]
        if problems:
            return problems
        keyed = [self._curve_id(curve) if isinstance(curve, dict) else (None, "not a row")
                 for curve in inputs["curves"]]
        problems = [f"curves[{n}] id cannot be a key: {why}" for n, (_, why) in enumerate(keyed)
                    if why not in (None, KEY_MISSING)]
        counts = Counter(key for key, why in keyed if not why)
        repeated = sorted({repr(key) for key, why in keyed if not why and counts[key] > 1})
        return problems + ([f"curves lists the id(s) {repeated} more than once: a contract could not say which "
                            "it reads"] if repeated else [])

    def _curve_id(self, curve):
        """Return ``(key, problem)`` of a curve row's id (:func:`fields_key`)."""
        return fields_key(curve, self.params["curve_id_fields"])

    def _survival(self, curve):
        """Return ``(CurveSurvival, None)`` for a curve row, or ``(None, status)``."""
        p = self.params
        if "eligible_field" in p and curve.get(p["eligible_field"]) is not True:
            return None, "ineligible_curve"
        reference = curve.get(p["reference_field"]) if p["axis"] == AXIS_LOG_RETURN else None
        values, probabilities = curve.get(p["values_field"]), curve.get(p["probabilities_field"])
        if CurveSurvival.problems(values, probabilities, p["axis"], reference):
            return None, "bad_curve"
        return CurveSurvival(values, probabilities, p["axis"], reference), None

    def _refusal(self, row, survival):
        """Return why a row with a usable curve cannot be priced, or None."""
        p = self.params
        name = row.get(p["payoff_field"])
        geometry = PAYOFFS.get(name) if isinstance(name, str) else None
        if geometry is None:
            return "unknown_payoff"
        given = {"lower": row.get(p["lower_field"]), "upper": row.get(p["upper_field"])}
        if geometry.bounds_problem(given["lower"], given["upper"]) is not None:
            return "bad_bounds"
        if not all(survival.covers(given[bound]) for bound in geometry.bounds):
            return "outside_support"
        return None

    def _priced(self, row, curves):
        """Write the fair-value pair onto ``row`` (a copy)."""
        p, name = self.params, self.params["fair_field"]
        key, why = fields_key(row, p["curve_key_fields"])
        if why is None:
            survival, status = curves.get(key, (None, "no_curve"))
        else:
            survival, status = None, ("no_curve" if why == KEY_MISSING else "bad_curve_key")
        if status is None:
            status = self._refusal(row, survival)
        fair = None
        if status is None:
            fair = PAYOFFS[row[p["payoff_field"]]].yes_probability(survival, row.get(p["lower_field"]),
                                                                   row.get(p["upper_field"]))
        row.update({name: fair, name + STATUS_SUFFIX: status or STATUS_OK})
        return row

    def run(self, ctx, inputs):
        """Price every contract row from its curve; rows that cannot be priced say why.

        Parameters
        ----------
        ctx : NodeContext
            Unused.
        inputs : dict
            ``records``: the contract rows; ``curves``: the curve rows.

        Returns
        -------
        dict
            ``{"records": [...], "census": {...}}``, new row dicts in input order.
        """
        keyed = ((self._curve_id(curve)[0], curve) for curve in inputs["curves"])
        curves = {key: self._survival(curve) for key, curve in keyed if key is not None}
        records = [self._priced(dict(row), curves) for row in inputs["records"]]
        statuses = [row[self.params["fair_field"] + STATUS_SUFFIX] for row in records]
        census = {"rows": len(records), "priced": statuses.count(STATUS_OK),
                  "by_status": {status: statuses.count(status) for status in STATUSES}}
        self.log.info("priced %d of %d row(s) from %d curve(s)", census["priced"], census["rows"], len(curves))
        return {"records": records, "census": census}


def _name_ok(value):
    """Say whether ``value`` is a non-empty string."""
    return isinstance(value, str) and bool(value)


def row_key(value):
    """Return ``(key, problem)`` for one cell used as an id, a chain key or a right: exactly one is None.

    The one owner of "do these two cells name the same thing" for the binary-pricing nodes
    (:class:`CurveBinaryFairValue` curve keys, ``digital_bounds.DigitalBounds`` chain keys and
    rights, ``libs.binary_coherence.BinaryCoherence`` ids). A key is a SCALAR, nothing else:

    - a ``str``, read by its characters (``str.__str__``, never ``str(x)``, so ``numpy.str_`` and a
      ``StrEnum`` whose ``__str__`` lies are the plain string they hold). No Unicode normalisation:
      an NFC and an NFD spelling are two keys;
    - a non-bool integer (``int`` and its subclasses such as ``IntEnum`` by ``int.__index__``,
      numpy ints by ``operator.index``), read to a plain ``int`` of any size.

    The key is that plain ``str`` or ``int``, so ``1`` and ``"1"`` are DIFFERENT keys. A missing
    cell is ``None`` (an absent column reads as ``None``) or the empty string, the CSV spelling of
    nothing: ``problem`` is then :data:`KEY_MISSING`. Everything else is refused by name
    (``problem`` starts ``"refused "``): ``bool`` and ``numpy.bool_``, every float (``-0.0``, NaN,
    numpy floats), ``Decimal``, pandas ``NA``/``NaT``, lists, tuples, dicts, sets, bytes, dates and
    any other object. A composite key is declared in config as a list of columns
    (:func:`fields_key`), never carried in one cell.

    Parameters
    ----------
    value : object
        Any cell.

    Returns
    -------
    tuple
        ``(key, None)`` with ``key`` a plain ``str`` or ``int``, or ``(None, problem)`` with
        ``problem`` :data:`KEY_MISSING` or ``"refused <module>.<type> ..."``.

    Examples
    --------
    One key however it is spelled, and never across types::

        row_key(numpy.int64(7)) == row_key(7)      # True: (7, None)
        row_key("1") == row_key(1)                 # False
        row_key("")
        # -> (None, 'missing')
        row_key(True)
        # -> (None, "refused builtins.bool (True): a key is a non-empty str or a non-bool int")
    """
    if value is None:
        return None, KEY_MISSING
    if isinstance(value, str):
        text = str.__str__(value)
        return (text, None) if text else (None, KEY_MISSING)
    if isinstance(value, bool):
        pass                                        # refused below, never the int it subclasses
    elif isinstance(value, int):
        return int.__index__(value), None           # an int subclass by its value, never its own __index__
    elif isinstance(value, numbers.Integral):       # numpy ints (numpy.bool_ is not Integral)
        try:
            return int.__index__(operator.index(value)), None
        except TypeError:
            pass
    kind = type(value)
    return None, (f"refused {kind.__module__}.{kind.__qualname__} ({value!r}): a key is a non-empty str or a "
                  "non-bool int")


def fields_key(row, fields):
    """Return ``(key, problem)`` for the composite key a row's ``fields`` spell, each read by :func:`row_key`.

    Parameters
    ----------
    row : dict
        The row; an absent column reads as missing.
    fields : sequence of str
        The key's columns, in the order the other side declares its own.

    Returns
    -------
    tuple
        ``(tuple of keys, None)``, or ``(None, problem)``: the first refused cell's problem (named
        with its column) when any cell is refused — a refusal wins over a missing cell, since it is a
        defect in the data, not an absence — else :data:`KEY_MISSING` when any is missing.

    Examples
    --------
    A chain named by its underlying and its expiry::

        fields_key({"root": "X", "expiry": "2026-10-16"}, ["root", "expiry"])
        # -> (('X', '2026-10-16'), None)
    """
    keys, missing = [], False
    for field in fields:
        key, problem = row_key(row.get(field))
        if problem == KEY_MISSING:
            missing = True
        elif problem is not None:
            return None, f"column {field!r} {problem}"
        keys.append(key)
    return (None, KEY_MISSING) if missing else (tuple(keys), None)


def key_fields_problems(params, left, right):
    """List what is wrong with two params naming the columns of one composite key, one list per side.

    Parameters
    ----------
    params : dict
        The node's params.
    left, right : str
        The two params' names; each must be a non-empty list of distinct, non-empty column names,
        and the two the same length (column ``i`` on one side is matched with column ``i`` on the other).

    Returns
    -------
    list of str
        Every problem found, empty when none.

    Examples
    --------
    Two sides of different lengths cannot be matched::

        key_fields_problems({"a": ["x"], "b": ["x", "y"]}, "a", "b")
        # -> ['a and b must list the same number of columns, got 1 and 2']
    """
    problems = []
    for name in (left, right):
        value = params.get(name)
        if not (isinstance(value, list) and value and all(_name_ok(f) for f in value)):
            problems.append(f"{name} is required: a non-empty list of non-empty column names, got {value!r}")
        elif len(set(value)) != len(value):
            problems.append(f"{name} lists a column more than once: {value!r}")
    if not problems and len(params[left]) != len(params[right]):
        problems.append(f"{left} and {right} must list the same number of columns, got {len(params[left])} and "
                        f"{len(params[right])}")
    return problems
