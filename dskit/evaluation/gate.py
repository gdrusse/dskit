"""The exact-match gate: event totals against expected values, or no report (ADR-0283).

A report is a pure function of its events, so before one is rendered a
document can prove the events add up to what the producer already
recorded. :class:`EventTotalsGate` folds the event list through
:class:`~dskit.evaluation.book.EvaluationBook` (the one P&L fold), reads
the named totals off the last point of the account and compares each to
a value wired in on the ``expected`` port. A total outside its tolerance
raises :class:`~dskit.evaluation.events.EvaluationError` with every miss
listed, so the node that renders the report downstream never runs and no
report file is written.

The expected values come from a wire (for instance a hash-pinned file's
own numbers), never from the node's params; the tolerances are the
document's, not the code's.

Import cost: stdlib plus ``dskit.evaluation`` and ``dskit.pipeline``.
"""

from __future__ import annotations

from dskit.evaluation.book import EvaluationBook
from dskit.evaluation.events import EvaluationError, EventLog
from dskit.pipeline.node import Node, reject_unknown_params
from dskit.pipeline.records import number_ok

__all__ = ["MEASURES", "EventTotalsGate", "GateRefusal"]

#: The totals a check may name, each read from ``(log, last account point)``:
#: money off the point, counts off the log.
MEASURES = {
    "net_pnl": lambda log, point: point.trading_pnl,
    "final_equity": lambda log, point: point.equity,
    "contributions": lambda log, point: point.external,
    "realised": lambda log, point: point.realised,
    "fees": lambda log, point: point.fees,
    "fill_count": lambda log, point: len(log.of_kind("fill")),
    "refusal_count": lambda log, point: len(log.of_kind("refusal")),
    "decision_count": lambda log, point: len(log.of_kind("decision")),
}

#: The keys one check declares.
_CHECK_KEYS = ("expected", "tolerance")


class GateRefusal(EvaluationError):
    """The gate's refusal: every miss listed under the gate's own header.

    Examples
    --------
    ::

        error = GateRefusal(["net_pnl: expected 1.0, got 2.0"])
        str(error).startswith("event totals gate refused")  # True
    """

    def __init__(self, problems):
        self.problems = list(problems)
        count = len(self.problems)
        ValueError.__init__(
            self, f"event totals gate refused ({count} miss{'es' if count != 1 else ''}):\n  "
            + "\n  ".join(self.problems))


class EventTotalsGate(Node):
    """Refuse unless the events' totals equal the expected values (role ``transform``).

    Parameters
    ----------
    params : dict
        ``checks`` (object, REQUIRED) — ``{measure: {"expected": key,
        "tolerance": t}}`` over :data:`MEASURES`. ``expected`` names a key
        of the ``expected`` input; ``tolerance`` (a finite number >= 0,
        required, no default) is the largest accepted absolute difference
        (a difference equal to it passes).

    Inputs
    ------
    ``events`` (REQUIRED)
        A list of schema-v1 event objects.
    ``expected`` (REQUIRED)
        ``{key: number}`` holding every key the checks name.

    Outputs
    -------
    ``events``
        The same list, untouched, to wire on to the report.
    ``comparison``
        One row per check: ``measure``, ``expected``, ``actual``, ``diff``,
        ``tolerance``, ``ok``.

    Raises
    ------
    GateRefusal
        (an ``EvaluationError``) When any total differs by more than its tolerance, or an expected
        value is missing or not a number. Every miss is listed.

    Examples
    --------
    ::

        node = EventTotalsGate("gate", {"checks": {
            "net_pnl": {"expected": "pnl", "tolerance": 0.005}}})
        out = node.run(ctx, {"events": events, "expected": {"pnl": 259.05}})
        out["comparison"][0]["ok"]  # True, or the run raised
    """

    role = "transform"
    outputs = ("events", "comparison")
    _PARAMS = ("checks", "notes")

    @classmethod
    def validate_params(cls, params):
        """Refuse unknown knobs, an unknown measure and a check without a tolerance.

        Parameters
        ----------
        params : dict

        Returns
        -------
        list of str
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        checks = params.get("checks")
        if not isinstance(checks, dict) or not checks:
            return problems + [f"checks is required: a non-empty object of measure -> "
                               f"check, got {checks!r}"]
        for measure, check in checks.items():
            problems.extend(cls._check_problems(measure, check))
        return problems

    @staticmethod
    def _check_problems(measure, check):
        """Problems with one ``checks`` entry."""
        problems = []
        if measure not in MEASURES:
            problems.append(f"checks.{measure} is not a measure — allowed: {sorted(MEASURES)}")
        if not isinstance(check, dict):
            return problems + [f"checks.{measure} must be an object, got {check!r}"]
        unknown = sorted(set(check) - set(_CHECK_KEYS))
        if unknown:
            problems.append(f"checks.{measure}: unknown key(s) {unknown} — "
                            f"allowed: {list(_CHECK_KEYS)}")
        key = check.get("expected")
        if not isinstance(key, str) or not key:
            problems.append(f"checks.{measure}.expected must be a non-empty string "
                            f"(a key of the expected input), got {key!r}")
        if "tolerance" not in check:
            problems.append(f"checks.{measure}.tolerance is required — the gate has no "
                            f"default slack")
        elif not number_ok(check["tolerance"]) or check["tolerance"] < 0:
            problems.append(f"checks.{measure}.tolerance must be a finite number >= 0, "
                            f"got {check['tolerance']!r}")
        return problems

    def _expected_problems(self, expected):
        """Problems with the ``expected`` input against the declared checks."""
        if not isinstance(expected, dict):
            return [f"expected must be an object of key -> number, "
                    f"got {type(expected).__name__}"]
        problems = []
        for key in sorted({check["expected"] for check in self.params["checks"].values()}):
            if key not in expected:
                problems.append(f"expected has no {key!r} (keys: {sorted(expected)})")
            elif not number_ok(expected[key]):
                problems.append(f"expected[{key!r}] must be a finite number, "
                                f"got {expected[key]!r}")
        return problems

    def validate_inputs(self, inputs):
        """Refuse an ``events`` port that is not a list of objects, and a bad ``expected``.

        Parameters
        ----------
        inputs : dict

        Returns
        -------
        list of str
        """
        events = inputs.get("events")
        if not isinstance(events, (list, tuple)) or not all(isinstance(e, dict) for e in events):
            return [f"events must be a list of event objects, got {type(events).__name__}"]
        return self._expected_problems(inputs.get("expected"))

    def run(self, ctx, inputs):
        """Fold the events, compare every total, raise on any miss, else pass them on.

        Parameters
        ----------
        ctx : NodeContext or None
        inputs : dict

        Returns
        -------
        dict
        """
        problems = self._expected_problems(inputs.get("expected"))
        if problems:
            raise GateRefusal(problems)
        log = EventLog(inputs["events"])
        points = EvaluationBook(log).points
        if not points:
            raise GateRefusal(["the events hold no fill, mark or cash flow: nothing to "
                              "compare"])
        rows = [self._row(measure, check, inputs["expected"], log, points[-1])
                for measure, check in self.params["checks"].items()]
        misses = [f"{row['measure']}: expected {row['expected']!r}, got {row['actual']!r} "
                  f"(difference {row['diff']!r} exceeds {row['tolerance']!r})"
                  for row in rows if not row["ok"]]
        if misses:
            raise GateRefusal(misses)
        return {"events": inputs["events"], "comparison": rows}

    @staticmethod
    def _row(measure, check, expected, log, point):
        """Compare one measure of the account's last point to its expected value."""
        found = MEASURES[measure](log, point)
        actual = found if isinstance(found, int) else float(found)
        want = expected[check["expected"]]
        diff = abs(actual - want)
        return {"measure": measure, "expected": want, "actual": actual, "diff": diff,
                "tolerance": check["tolerance"], "ok": diff <= check["tolerance"]}
