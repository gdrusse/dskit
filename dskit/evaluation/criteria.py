"""Pre-registered pass/fail criteria and the verdict they return.

A criterion is written BEFORE the run (it rides in ``run_start``) and says
which statistic must clear which threshold on at least how many
observations. The verdict is three-valued on purpose: a Sharpe of 3 on
four days is not a PASS, it is INCONCLUSIVE, and a report that cannot say
so invites reading noise as edge. A statistic the table cannot compute
(no losing trade, no variance) is INCONCLUSIVE for the same reason.

A criterion naming a statistic the table does not define is a config error
and is refused when the scorecard is built — a typo'd criterion that
silently never fails is worse than no criterion.

Import cost: stdlib plus ``dskit.pipeline``.
"""

from __future__ import annotations

import operator
from dataclasses import dataclass

from dskit.pipeline.base import ConfigError
from dskit.pipeline.records import number_ok

__all__ = ["OPERATORS", "STATUSES", "Criterion", "Scorecard", "Verdict"]

#: The comparison a criterion may state, keyed by its spelling.
OPERATORS = {">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt}

#: A criterion's verdicts, worst first — the scorecard's overall status is
#: the worst member present.
STATUSES = ("FAIL", "INCONCLUSIVE", "PASS")

#: The overall status of a run that registered no criteria.
_UNJUDGED = "UNJUDGED"

#: A criterion's closed field set.
_FIELDS = ("name", "stat", "op", "value", "min_n")


@dataclass(frozen=True)
class Criterion:
    """One pre-registered threshold on one statistic.

    Parameters
    ----------
    name : str
        What the criterion is called in the report.
    stat : str
        A statistic name from the report's table.
    op : str
        One of :data:`OPERATORS`.
    value : float
        The threshold.
    min_n : int
        The sample the statistic needs before the criterion can PASS or
        FAIL; below it the verdict is INCONCLUSIVE.

    Examples
    --------
    ::

        criterion = Criterion("edge", "trade_mean", ">", 0.0, 30)
        criterion.op  # '>'
    """

    name: str
    stat: str
    op: str
    value: float
    min_n: int

    def __post_init__(self):
        """Refuse an invalid criterion with every problem."""
        problems = Criterion.problems(
            {"name": self.name, "stat": self.stat, "op": self.op,
             "value": self.value, "min_n": self.min_n},
            "criterion",
        )
        if problems:
            raise ConfigError(problems)

    @staticmethod
    def problems(obj, where="criterion"):
        """List every problem with ``obj`` as a criterion object.

        Parameters
        ----------
        obj : dict
            ``{name, stat, op, value, min_n}``, nothing else.
        where : str
            The prefix naming it.

        Returns
        -------
        list of str
        """
        if not isinstance(obj, dict):
            return [f"{where} must be an object, got {obj!r}"]
        problems = []
        unknown = sorted(set(obj) - set(_FIELDS))
        missing = [name for name in _FIELDS if name not in obj]
        if unknown:
            problems.append(f"{where}: unknown field(s) {unknown} — allowed: {list(_FIELDS)}")
        if missing:
            problems.append(f"{where}: missing field(s) {missing}")
        for name in ("name", "stat"):
            if name in obj and (not isinstance(obj[name], str) or not obj[name]):
                problems.append(f"{where}.{name} must be a non-empty string")
        if "op" in obj and obj["op"] not in OPERATORS:
            problems.append(f"{where}.op must be one of {list(OPERATORS)}, got {obj['op']!r}")
        if "value" in obj and not number_ok(obj["value"]):
            problems.append(f"{where}.value must be a finite number, got {obj['value']!r}")
        min_n = obj.get("min_n")
        if "min_n" in obj and (isinstance(min_n, bool) or not isinstance(min_n, int)
                               or min_n < 0):
            problems.append(f"{where}.min_n must be an int >= 0, got {min_n!r}")
        return problems

    @classmethod
    def from_obj(cls, obj):
        """Build a criterion from its JSON object.

        Parameters
        ----------
        obj : dict

        Returns
        -------
        Criterion

        Raises
        ------
        ConfigError
            Listing every problem.
        """
        problems = cls.problems(obj)
        if problems:
            raise ConfigError(problems)
        return cls(**{name: obj[name] for name in _FIELDS})

    def to_obj(self):
        """Return the criterion as its JSON object.

        Returns
        -------
        dict
        """
        return {name: getattr(self, name) for name in _FIELDS}

    def evaluate(self, statistic):
        """Judge one statistic against this criterion.

        Parameters
        ----------
        statistic : object
            Carries ``value`` (float or None) and ``n`` (int or None — no
            sample requirement applies to a statistic without one).

        Returns
        -------
        Verdict
        """
        value, n = statistic.value, statistic.n
        if value is None:
            return Verdict(self, "INCONCLUSIVE", value, n, "statistic undefined on this sample")
        if n is not None and n < self.min_n:
            return Verdict(self, "INCONCLUSIVE", value, n, f"n={n} < min_n={self.min_n}")
        passed = OPERATORS[self.op](value, self.value)
        status = "PASS" if passed else "FAIL"
        return Verdict(self, status, value, n, f"{value:.6g} {self.op} {self.value:.6g}"
                       + ("" if passed else " is false"))


@dataclass(frozen=True)
class Verdict:
    """One criterion's result.

    Parameters
    ----------
    criterion : Criterion
    status : str
        One of :data:`STATUSES`.
    observed : float or None
        The statistic's value.
    n : int or None
        Its sample size.
    reason : str
        Why this status, in words.

    Examples
    --------
    ::

        Criterion("edge", "trade_mean", ">", 0.0, 30).evaluate(stat).status  # 'INCONCLUSIVE'
    """

    criterion: Criterion
    status: str
    observed: object
    n: object
    reason: str

    def to_obj(self):
        """Return the verdict as a JSON-ready dict.

        Returns
        -------
        dict
        """
        return {**self.criterion.to_obj(), "status": self.status,
                "observed": self.observed, "n": self.n, "reason": self.reason}


class Scorecard:
    """Every criterion judged against a statistics table, and the overall status.

    Parameters
    ----------
    criteria : iterable of Criterion
    table : object
        Answers ``get(name)`` with a statistic (``value``, ``n``) or None,
        and ``names`` with every name it defines.

    Raises
    ------
    ConfigError
        When a criterion names a statistic the table does not define.

    Examples
    --------
    ::

        card = Scorecard([Criterion("edge", "trade_mean", ">", 0.0, 30)], table)
        card.status  # 'INCONCLUSIVE' on a short run
    """

    def __init__(self, criteria, table):
        self.criteria = tuple(criteria)
        unknown = sorted({c.stat for c in self.criteria} - set(table.names))
        if unknown:
            raise ConfigError(
                [f"criteria name unknown statistic(s) {unknown} — known: {sorted(table.names)}"]
            )
        self.verdicts = tuple(c.evaluate(table.get(c.stat)) for c in self.criteria)

    @property
    def status(self):
        """FAIL if any criterion failed, else INCONCLUSIVE if any was, else PASS.

        ``UNJUDGED`` when the run registered no criteria.
        """
        if not self.verdicts:
            return _UNJUDGED
        present = {verdict.status for verdict in self.verdicts}
        return next(status for status in STATUSES if status in present)

    def to_obj(self):
        """Return the scorecard as a JSON-ready dict.

        Returns
        -------
        dict
            ``status`` and ``verdicts``.
        """
        return {"status": self.status, "verdicts": [v.to_obj() for v in self.verdicts]}
