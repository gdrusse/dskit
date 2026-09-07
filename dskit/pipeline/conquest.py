"""Per-(unit,horizon) prediction-quality gate that caps the horizon (ADR-0107).

A horizon-conquest node walks each prediction unit's horizons in ascending
order and caps the unit at the furthest contiguous horizon whose prediction
passes every declared check. It is generic and config-driven: the checks, the
pass rule per check, and the alpha are config, while the contiguity walk is
fixed. A ``slice_field`` adds regime stability — a horizon clears only when it
clears inside every slice — so "good at 10am, useless at 3pm" cannot ship.

The single owner of the contiguity rule. A unit whose horizon 1 already fails
caps at ``0`` (no horizon served); a unit whose every horizon passes caps at
its maximum. The node never re-fits, re-searches, or promotes — it reads
per-horizon evidence and writes a verdict. Evidence is fail-loud: a duplicate
``(unit, horizon[, slice])`` row, a non-finite metric, a gapped or
non-1-starting horizon ladder, or (under a slice) a horizon missing one of a
unit's canonical slices is refused, never silently averaged or skipped — a
cap is a claim that every horizon up to it passed, so a gap means that claim
was never tested.
"""

from __future__ import annotations

from dskit.pipeline.node import Node, reject_unknown_params
from dskit.pipeline.records import number_ok

__all__ = ["HorizonConquest"]

_PASS_IFS = ("positive", "negative", "boolean", "p_below", "p_above")
_DEFAULT_PASS_IF = "positive"
_DEFAULT_ALPHA = 0.05
_CHECK_FIELDS = frozenset({"metric", "pass_if", "alpha"})


class HorizonConquest(Node):
    """Cap each unit's horizon at the furthest contiguous passing horizon.

    Consumes a list of evidence rows — one dict per (unit, horizon) when no
    slice is declared, or one per (unit, horizon, slice) when ``slice_field``
    is — and returns, per unit, the furthest horizon reached before the
    first fail, the first failing horizon, how many horizons passed, and
    which named checks (and, when ``slice_field`` is set, which named
    slices) backed that cap. Horizons are walked ascending regardless of
    row order, but every horizon from 1 up to the unit's furthest evidenced
    horizon must actually be present — a gap is refused, never silently
    treated as a shorter ladder.

    Parameters
    ----------
    params : dict
        ``checks`` (required) — a non-empty list of check objects, each
        ``{"metric": <row field>, "pass_if": <rule>, "alpha": <num>}`` where
        ``pass_if`` is one of ``positive`` (default; value must be a finite
        number > 0), ``negative`` (finite < 0), ``boolean`` (value must be an
        actual ``True``/``False``), ``p_below`` (finite ``<= alpha``), and
        ``p_above`` (finite ``>= alpha``). ``alpha`` (default 0.05) feeds
        ``p_below``/``p_above``. A horizon passes only when every check
        passes. ``unit_field`` (default ``"unit"``) and ``horizon_field``
        (default ``"horizon"``) name the row keys. ``slice_field`` (optional)
        names a slicing key: when set, a horizon passes only if it passes in
        every slice the unit has ever seen, and a horizon missing any of
        those slices fails closed (a silent gap would ship "good at 10am,
        useless at 3pm").

    Examples
    --------
    Cap AAPL at horizon 2 when horizon 3 first fails::

        node = HorizonConquest("gate", {
            "checks": [{"metric": "improvement", "pass_if": "positive"}],
        })
        out = node.run(ctx, {"records": [
            {"unit": "AAPL", "horizon": 1, "improvement": 0.2},
            {"unit": "AAPL", "horizon": 2, "improvement": 0.1},
            {"unit": "AAPL", "horizon": 3, "improvement": -0.1},
        ]})
        # -> out["caps"][0]["capped_horizon"] == 2
    """

    role = "score"
    outputs = ("caps", "metrics")
    _PARAMS = ("checks", "unit_field", "horizon_field", "slice_field")

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            The node's declared params, straight from the document.

        Returns
        -------
        list of str
            One problem per malformed knob: an unknown top-level param, an
            unknown or malformed ``checks`` entry, and a non-string
            ``unit_field``/``horizon_field``/``slice_field``.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        checks = params.get("checks")
        if not isinstance(checks, list) or not checks:
            problems.append("checks must be a non-empty list")
        else:
            for index, check in enumerate(checks):
                where = f"checks[{index}]"
                if not isinstance(check, dict):
                    problems.append(f"{where} must be an object")
                    continue
                check_problems = []
                reject_unknown_params(check_problems, check, _CHECK_FIELDS)
                problems.extend(f"{where} {p}" for p in check_problems)
                if not isinstance(check.get("metric"), str) or not check["metric"]:
                    problems.append(f"{where}.metric must be a non-empty string")
                rule = check.get("pass_if", _DEFAULT_PASS_IF)
                if rule not in _PASS_IFS:
                    problems.append(f"{where}.pass_if must be one of {sorted(_PASS_IFS)}")
                alpha = check.get("alpha", _DEFAULT_ALPHA)
                if not number_ok(alpha) or not 0.0 < alpha < 1.0:
                    problems.append(f"{where}.alpha must be a number in (0, 1)")
        for field in ("unit_field", "horizon_field", "slice_field"):
            value = params.get(field)
            if value is not None and (not isinstance(value, str) or not value):
                problems.append(f"{field} must be a non-empty string")
        return problems

    def validate_inputs(self, inputs):
        """Problems with the materialized ``inputs``, empty when none.

        Parameters
        ----------
        inputs : dict
            Must contain exactly the ``records`` port.

        Returns
        -------
        list of str
            One problem when ``inputs`` is not exactly ``{"records": ...}``,
            or when ``records`` did not materialize as a list.
        """
        if not isinstance(inputs, dict) or set(inputs) != {"records"}:
            return ["inputs must contain exactly records"]
        if not isinstance(inputs["records"], list):
            return ["records must materialize as a list"]
        return []

    @staticmethod
    def _passes(value, rule, alpha):
        """Say whether one already-typed metric value clears ``rule``."""
        if rule == "boolean":
            if not isinstance(value, bool):
                raise ValueError(f"boolean check got non-boolean {value!r}")
            return value
        if rule == "p_below":
            return value <= alpha
        if rule == "p_above":
            return value >= alpha
        if rule == "negative":
            return value < 0.0
        return value > 0.0

    def run(self, ctx, inputs):
        """Walk each unit's horizons ascending and cap at the first fail.

        Parameters
        ----------
        ctx : dskit.pipeline.node.NodeContext
            The run frame; unused — the gate reads only the wired evidence
            rows.
        inputs : dict
            ``records``, the evidence rows.

        Returns
        -------
        dict
            ``caps`` — one verdict per unit: ``unit``, ``capped_horizon``,
            ``first_failing_horizon``, ``n_passed``, ``n_horizons``,
            ``passing_checks`` (the check metric names satisfied at the
            cap), and — only when ``slice_field`` is set — ``slice_evidence``
            (each of the unit's canonical slices' pass/fail at the cap).
            ``metrics`` — ``n_units``, ``n_capped_units``,
            ``n_horizons_passed``.

        Raises
        ------
        ValueError
            When a row is not an object, a ``unit``/``horizon``/``slice``
            value is unusable, a check's declared metric is absent or the
            wrong shape for its rule, a duplicate ``(unit, horizon[,
            slice])`` row appears, a horizon is missing evidence for one of
            its unit's canonical slices, or a unit's evidenced horizons are
            not a dense ladder starting at 1.
        """
        del ctx
        checks = self.params["checks"]
        unit_field = self.params.get("unit_field", "unit")
        horizon_field = self.params.get("horizon_field", "horizon")
        slice_field = self.params.get("slice_field")
        # units[(unit, horizon[, slice])] -> list of per-check verdicts (bool).
        units = {}
        # unit -> the canonical ordered set of slices that unit has ever seen.
        unit_slices = {}
        for row in inputs["records"]:
            if not isinstance(row, dict):
                raise ValueError("evidence rows must be objects")
            unit = row.get(unit_field)
            horizon = row.get(horizon_field)
            if not isinstance(unit, (str, int)) or isinstance(unit, bool):
                raise ValueError(f"{unit_field} must be a string or int, got {unit!r}")
            if unit == "" and not isinstance(unit, int):
                raise ValueError(f"{unit_field} must not be empty, got {unit!r}")
            if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1:
                raise ValueError(f"horizon must be a positive int, got {horizon!r}")
            results = []
            for check in checks:
                value = row.get(check["metric"])
                if value is None:
                    raise ValueError(
                        f"evidence row is missing {check['metric']!r}: {row!r}"
                    )
                rule = check.get("pass_if", _DEFAULT_PASS_IF)
                alpha = check.get("alpha", _DEFAULT_ALPHA)
                if rule != "boolean" and not number_ok(value):
                    raise ValueError(
                        f"{check['metric']!r} must be a finite number under "
                        f"{rule!r}, got {value!r}"
                    )
                results.append(self._passes(value, rule, alpha))
            slice_value = None
            if slice_field is not None:
                slice_value = row.get(slice_field)
                if not isinstance(slice_value, (str, int)) or isinstance(slice_value, bool):
                    raise ValueError(
                        f"{slice_field} must be a string or int, got {slice_value!r}"
                    )
                if slice_value == "" and not isinstance(slice_value, int):
                    raise ValueError(
                        f"{slice_field} must not be empty, got {slice_value!r}"
                    )
            key = (unit, horizon, slice_value) if slice_field is not None else (unit, horizon)
            if key in units:
                raise ValueError(f"duplicate evidence row for {key!r}")
            units[key] = results
            if slice_field is not None:
                unit_slices.setdefault(unit, set()).add(slice_value)

        caps = []
        total_passed = 0
        for unit in sorted({key[0] for key in units}, key=str):
            horizon_keys = sorted({key[1] for key in units if key[0] == unit})
            if horizon_keys != list(range(1, len(horizon_keys) + 1)):
                present = set(horizon_keys)
                missing = [
                    h for h in range(1, horizon_keys[-1] + 1) if h not in present
                ]
                raise ValueError(
                    f"unit {unit!r} has a gapped horizon ladder {horizon_keys}: "
                    f"missing horizon(s) {missing} — a cap claims every "
                    f"horizon up to it passed, so the ladder must be dense "
                    f"from 1"
                )
            canonical_slices = sorted(unit_slices.get(unit, set()), key=str)
            capped = 0
            first_fail = None
            passed = 0
            passing_checks = []
            slice_evidence = {}
            for horizon in horizon_keys:
                if slice_field is not None:
                    missing_slices = [
                        sl for sl in canonical_slices
                        if (unit, horizon, sl) not in units
                    ]
                    if missing_slices:
                        raise ValueError(
                            f"unit {unit!r} horizon {horizon} is missing "
                            f"evidence for slice(s) {missing_slices}"
                        )
                    per_slice = {
                        sl: all(units[(unit, horizon, sl)]) for sl in canonical_slices
                    }
                    per_check = [
                        all(units[(unit, horizon, sl)][i] for sl in canonical_slices)
                        for i in range(len(checks))
                    ]
                    good = all(per_check)
                else:
                    per_check = units[(unit, horizon)]
                    good = all(per_check)
                if not good:
                    first_fail = horizon
                    break
                capped = horizon
                passed += 1
                total_passed += 1
                passing_checks = [
                    check["metric"] for check, ok in zip(checks, per_check) if ok
                ]
                if slice_field is not None:
                    slice_evidence = {str(sl): per_slice[sl] for sl in canonical_slices}
            verdict = {
                "unit": unit,
                "capped_horizon": capped,
                "first_failing_horizon": first_fail,
                "n_passed": passed,
                "n_horizons": len(horizon_keys),
                "passing_checks": passing_checks,
            }
            if slice_field is not None:
                verdict["slice_evidence"] = slice_evidence
            caps.append(verdict)
        return {
            "caps": caps,
            "metrics": {
                "n_units": len(caps),
                "n_capped_units": sum(
                    1 for row in caps if row["first_failing_horizon"] is not None
                ),
                "n_horizons_passed": total_passed,
            },
        }
