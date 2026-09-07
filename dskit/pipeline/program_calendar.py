"""Machine-readable temporal contracts for staged model benchmarks (ADR-0098)."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import date, timedelta

from dskit.pipeline.stages import Stage, reject_unknown_params

__all__ = ["ProgramCalendar", "calendar_problems", "load_program_calendar"]

_TOP_FIELDS = frozenset(
    {"schema_version", "name", "timezone", "date_semantics", "fold_schedules", "periods", "phases", "locks"}
)
_FOLD_FIELDS = frozenset(
    {"objective", "select", "first", "step_days", "count", "val_days", "embargo_days", "train_days", "last_validation_end_exclusive"}
)
_PERIOD_FIELDS = frozenset(
    {"start", "end", "purpose", "selection_allowed", "fit_allowed"}
)
_PHASE_FIELDS = frozenset(
    {"order", "purpose", "uses_periods", "fold_schedule", "latest_asof", "selection_allowed", "fit_allowed", "calibration_allowed", "evaluation_claim"}
)
_LOCK_FIELDS = frozenset(
    {"selection_data_end", "lockbox_start", "mean_frozen_after", "uncertainty_frozen_after", "bundle_frozen_before", "production_not_before"}
)


def _string(value):
    return isinstance(value, str) and bool(value.strip())


def _date_problem(value):
    if not _string(value):
        return True
    try:
        date.fromisoformat(value)
    except ValueError:
        return True
    return False


def _exact_fields(problems, where, value, wanted):
    if not isinstance(value, dict):
        problems.append(f"{where} must be an object")
        return False
    missing = sorted(wanted - set(value))
    unknown = sorted(set(value) - wanted)
    if missing:
        problems.append(f"{where} is missing field(s) {missing}")
    if unknown:
        problems.append(f"{where} has unknown field(s) {unknown}")
    return not missing and not unknown


def calendar_problems(calendar):
    """Return every shape, reference, chronology, and fold-geometry defect."""
    problems = []
    if not _exact_fields(problems, "calendar", calendar, _TOP_FIELDS):
        return problems
    if calendar["schema_version"] != 1:
        problems.append("calendar.schema_version must equal 1")
    for field in ("name", "timezone", "date_semantics"):
        if not _string(calendar[field]):
            problems.append(f"calendar.{field} must be a non-empty string")

    schedules = calendar["fold_schedules"]
    if not isinstance(schedules, dict) or not schedules:
        problems.append("calendar.fold_schedules must be a non-empty object")
        schedules = {}
    for key, schedule in schedules.items():
        where = f"calendar.fold_schedules.{key}"
        if not _string(key) or not _exact_fields(problems, where, schedule, _FOLD_FIELDS):
            continue
        for field in ("first", "last_validation_end_exclusive"):
            if _date_problem(schedule[field]):
                problems.append(f"{where}.{field} must be a real YYYY-MM-DD date")
        for field in ("step_days", "count", "val_days", "embargo_days", "train_days"):
            value = schedule[field]
            floor = 0 if field == "embargo_days" else 1
            if not isinstance(value, int) or isinstance(value, bool) or value < floor:
                problems.append(f"{where}.{field} must be an integer >= {floor}")
        if schedule["select"] not in {"min", "max"}:
            problems.append(f"{where}.select must be min or max")
        if not _string(schedule["objective"]):
            problems.append(f"{where}.objective must be a non-empty string")
        numeric = all(isinstance(schedule[field], int) and not isinstance(schedule[field], bool) for field in ("step_days", "count", "val_days"))
        if not _date_problem(schedule["first"]) and numeric:
            expected = date.fromisoformat(schedule["first"]) + timedelta(
                days=schedule["step_days"] * (schedule["count"] - 1) + schedule["val_days"]
            )
            if expected.isoformat() != schedule["last_validation_end_exclusive"]:
                problems.append(
                    f"{where}.last_validation_end_exclusive must be {expected.isoformat()} for the declared folds"
                )

    periods = calendar["periods"]
    if not isinstance(periods, dict) or not periods:
        problems.append("calendar.periods must be a non-empty object")
        periods = {}
    for key, period in periods.items():
        where = f"calendar.periods.{key}"
        if not _string(key) or not _exact_fields(problems, where, period, _PERIOD_FIELDS):
            continue
        if _date_problem(period["start"]):
            problems.append(f"{where}.start must be a real YYYY-MM-DD date")
        if period["end"] is not None and _date_problem(period["end"]):
            problems.append(f"{where}.end must be null or a real YYYY-MM-DD date")
        if period["end"] is not None and not _date_problem(period["start"]) and not _date_problem(period["end"]) and period["start"] > period["end"]:
            problems.append(f"{where}.start must not follow end")
        if not _string(period["purpose"]):
            problems.append(f"{where}.purpose must be a non-empty string")
        for field in ("selection_allowed", "fit_allowed"):
            if not isinstance(period[field], bool):
                problems.append(f"{where}.{field} must be boolean")

    phases = calendar["phases"]
    if not isinstance(phases, dict) or not phases:
        problems.append("calendar.phases must be a non-empty object")
        phases = {}
    orders = []
    for key, phase in phases.items():
        where = f"calendar.phases.{key}"
        if not _string(key) or not _exact_fields(problems, where, phase, _PHASE_FIELDS):
            continue
        if not isinstance(phase["order"], int) or isinstance(phase["order"], bool) or phase["order"] < 1:
            problems.append(f"{where}.order must be a positive integer")
        else:
            orders.append(phase["order"])
        if not _string(phase["purpose"]) or not _string(phase["evaluation_claim"]):
            problems.append(f"{where}.purpose and evaluation_claim must be non-empty strings")
        if _date_problem(phase["latest_asof"]):
            problems.append(f"{where}.latest_asof must be a real YYYY-MM-DD date")
        refs = phase["uses_periods"]
        if not isinstance(refs, list) or any(not _string(ref) for ref in refs):
            problems.append(f"{where}.uses_periods must be a list of strings")
        elif set(refs) - set(periods):
            problems.append(f"{where}.uses_periods references missing period(s) {sorted(set(refs) - set(periods))}")
        fold = phase["fold_schedule"]
        if fold is not None and fold not in schedules:
            problems.append(f"{where}.fold_schedule references missing schedule {fold!r}")
        for field in ("selection_allowed", "fit_allowed", "calibration_allowed"):
            if not isinstance(phase[field], bool):
                problems.append(f"{where}.{field} must be boolean")
    if len(orders) != len(set(orders)):
        problems.append("calendar.phases orders must be unique")

    locks = calendar["locks"]
    if _exact_fields(problems, "calendar.locks", locks, _LOCK_FIELDS):
        for field in sorted(_LOCK_FIELDS):
            if _date_problem(locks[field]):
                problems.append(f"calendar.locks.{field} must be a real YYYY-MM-DD date")
        if not any(_date_problem(locks[field]) for field in _LOCK_FIELDS):
            if date.fromisoformat(locks["selection_data_end"]) + timedelta(days=1) != date.fromisoformat(locks["lockbox_start"]):
                problems.append("calendar lockbox must begin one day after selection_data_end")
            if locks["mean_frozen_after"] != locks["selection_data_end"]:
                problems.append("calendar mean_frozen_after must equal selection_data_end")
            if date.fromisoformat(locks["uncertainty_frozen_after"]) + timedelta(days=1) != date.fromisoformat(locks["bundle_frozen_before"]):
                problems.append("calendar bundle must freeze the day after uncertainty calibration ends")
            if not (
                locks["selection_data_end"] < locks["lockbox_start"]
                <= locks["uncertainty_frozen_after"]
                < locks["bundle_frozen_before"]
                <= locks["production_not_before"]
            ):
                problems.append("calendar locks must be chronologically ordered")

            selection_end = date.fromisoformat(locks["selection_data_end"])
            lockbox_start = date.fromisoformat(locks["lockbox_start"])
            uncertainty_end = date.fromisoformat(locks["uncertainty_frozen_after"])
            for key, phase in phases.items():
                if _date_problem(phase.get("latest_asof")):
                    continue
                latest = date.fromisoformat(phase["latest_asof"])
                refs = phase.get("uses_periods")
                if not isinstance(refs, list) or any(ref not in periods for ref in refs):
                    continue
                finite_periods = []
                for ref in refs:
                    period = periods[ref]
                    if _date_problem(period.get("start")):
                        continue
                    start = date.fromisoformat(period["start"])
                    end_value = period.get("end")
                    end = None if end_value is None or _date_problem(end_value) else date.fromisoformat(end_value)
                    if end is not None:
                        finite_periods.append((start, end))
                        if end > latest:
                            problems.append(
                                f"calendar.phases.{key}.latest_asof precedes period {ref!r} end"
                            )
                    elif latest < start:
                        problems.append(
                            f"calendar.phases.{key}.latest_asof precedes open period {ref!r} start"
                        )
                if phase.get("selection_allowed") is True:
                    if latest > selection_end:
                        problems.append(
                            f"calendar.phases.{key}.latest_asof exceeds selection_data_end"
                        )
                    if any(end >= lockbox_start for _start, end in finite_periods):
                        problems.append(
                            f"calendar.phases.{key} selects inside the lockbox"
                        )
                if phase.get("calibration_allowed") is True and latest > uncertainty_end:
                    problems.append(
                        f"calendar.phases.{key}.latest_asof exceeds uncertainty_frozen_after"
                    )
                fold_key = phase.get("fold_schedule")
                if fold_key in schedules and finite_periods:
                    schedule = schedules[fold_key]
                    if _date_problem(schedule.get("first")) or _date_problem(schedule.get("last_validation_end_exclusive")):
                        continue
                    first = date.fromisoformat(schedule["first"])
                    last = date.fromisoformat(schedule["last_validation_end_exclusive"]) - timedelta(days=1)
                    allowed_start = min(start for start, _end in finite_periods)
                    allowed_end = max(end for _start, end in finite_periods)
                    if first < allowed_start or last > allowed_end:
                        problems.append(
                            f"calendar.phases.{key} fold schedule extends outside its declared periods"
                        )
                    if phase.get("selection_allowed") is not True:
                        problems.append(
                            f"calendar.phases.{key} has folds but selection_allowed is not true"
                        )
    return problems


def load_program_calendar(source_path, declared):
    """Load a calendar relative to its declaring document and return its hash."""
    path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(source_path)), declared))
    with open(path, encoding="utf-8") as handle:
        calendar = json.load(handle)
    problems = calendar_problems(calendar)
    if problems:
        raise ValueError("invalid program calendar: " + "; ".join(problems))
    with open(path, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    return calendar, digest


class ProgramCalendar(Stage):
    """Pin one validated program phase before a benchmark may be planned."""

    outputs = ("calendar", "calendar_sha256", "phase")
    _PARAMS = ("path", "phase")

    @classmethod
    def validate_params(cls, params):
        """Require both knobs as non-empty strings and refuse the rest.

        Parameters
        ----------
        params : dict
            ``path`` and ``phase``, both required.

        Returns
        -------
        list of str
            Every problem found, empty when the params are legal.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        for field in cls._PARAMS:
            if not _string(params.get(field)):
                problems.append(f"{field} must be a non-empty string")
        return problems

    def validate_inputs(self, inputs):
        """Refuse any input: the calendar is read from the declared path alone.

        Parameters
        ----------
        inputs : dict

        Returns
        -------
        list of str
            Every problem found, empty when the inputs are legal.
        """
        return [] if inputs == {} else ["calendar takes no inputs"]

    def run(self, ctx, inputs):
        """Load the declared phase of the program calendar and its digest.

        Parameters
        ----------
        ctx : NodeContext
            Its ``source_path`` anchors the relative calendar path.
        inputs : dict
            Unused; the stage takes none.

        Returns
        -------
        dict
            The phase, the whole calendar and the file's sha256, so a reader
            can prove which calendar a run was planned against.
        """
        del inputs
        calendar, digest = load_program_calendar(ctx.source_path, self.params["path"])
        key = self.params["phase"]
        if key not in calendar["phases"]:
            raise ValueError(f"program calendar has no phase {key!r}")
        phase = {"key": key, **copy.deepcopy(calendar["phases"][key])}
        if phase["fold_schedule"] is not None:
            phase["walkforward"] = copy.deepcopy(calendar["fold_schedules"][phase["fold_schedule"]])
        return {"calendar": calendar, "calendar_sha256": digest, "phase": phase}
