"""The predictive program's calendar is the only active date authority."""

from __future__ import annotations

import json
import os


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS = os.path.join(ROOT, "configs")


def _raw(name):
    with open(os.path.join(CONFIGS, name), encoding="utf-8") as handle:
        return json.load(handle)


def _walk(name):
    return {
        key: value
        for key, value in _raw(name)["walkforward"].items()
        if key != "notes"
    }


def test_gate_documents_equal_the_program_calendar():
    calendar = _raw("program-calendar.json")
    outer = calendar["fold_schedules"]["development_outer"]
    wanted = {
        key: value
        for key, value in outer.items()
        if key != "last_validation_end_exclusive"
    }
    for name in (
        "run-p10-modelability.json",
        "run-p11-modelability.json",
        "run-p12-modelability.json",
    ):
        assert _walk(name) == wanted, name


def test_calendar_pins_the_full_forward_sequence():
    calendar = _raw("program-calendar.json")
    assert calendar["periods"]["development_validation"]["end"] == "2025-10-16"
    assert calendar["periods"]["final_hpo_validation"]["start"] == "2025-12-02"
    assert calendar["periods"]["final_hpo_validation"]["end"] == "2026-02-28"
    calibration = calendar["periods"][
        "mean_confirmation_uncertainty_calibration"
    ]
    assert calibration["start"] == "2026-03-01"
    assert calibration["end"] == "2026-05-31"
    simulation = calendar["periods"]["full_system_simulation"]
    assert simulation["start"] == "2026-06-01"
    assert simulation["end"] == "2026-08-31"
    assert calendar["locks"]["bundle_frozen_before"] == "2026-06-01"
    assert calendar["locks"]["production_not_before"] == "2026-09-01"


def test_p13_materialization_and_plan_share_the_locked_calendar():
    stages = _raw("run-p13-model-zoo.json")["stages"]
    assert stages["calendar"]["uses"] == (
        "dskit.pipeline.program_calendar:ProgramCalendar"
    )
    assert stages["calendar"]["params"] == {
        "path": "program-calendar.json",
        "phase": "model_zoo",
    }
    assert stages["plan"]["inputs"] == {
        "phase": "$calendar.phase",
        "calendar_sha256": "$calendar.calendar_sha256",
        "candidates": "$materialize.candidates",
    }


def test_p13_is_plan_only_until_its_inventory_is_reviewed():
    stages = _raw("run-p13-model-zoo.json")["stages"]
    approval = stages["approval"]
    assert approval["inputs"] == {"inventory_sha256": "$plan.inventory_sha256"}
    assert approval["params"]["approved_inventory_sha256"] == (
        "PENDING-PLAN-REVIEW"
    )
    assert stages["run"]["inputs"]["approval"] == "$approval.approval"
    assert stages["compare"]["inputs"]["approval"] == "$approval.approval"
