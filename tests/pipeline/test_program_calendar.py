"""ADR-0098 temporal-contract tests."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from dskit.pipeline.program_calendar import ProgramCalendar
from dskit.pipeline.stages import StageContext


def _context(tmp_path):
    return StageContext(
        document=SimpleNamespace(hash="b" * 64),
        source_path=str(tmp_path / "suite.json"),
        asof="2026-02-28",
        key="calendar",
        run_dir=str(tmp_path),
        artifact_dir=str(tmp_path),
    )


def _calendar(last_end="2025-10-17"):
    return {
        "schema_version": 1,
        "name": "example",
        "timezone": "America/New_York",
        "date_semantics": "inclusive except end_exclusive",
        "fold_schedules": {
            "outer": {
                "objective": "$score.metrics.ic",
                "select": "max",
                "first": "2022-05-06",
                "step_days": 63,
                "count": 20,
                "val_days": 63,
                "embargo_days": 5,
                "train_days": 730,
                "last_validation_end_exclusive": last_end,
            }
        },
        "periods": {
            "development": {
                "start": "2022-05-06",
                "end": "2025-10-16",
                "purpose": "selection",
                "selection_allowed": True,
                "fit_allowed": True,
            }
        },
        "phases": {
            "model_zoo": {
                "order": 1,
                "purpose": "selection",
                "uses_periods": ["development"],
                "fold_schedule": "outer",
                "latest_asof": "2026-02-28",
                "selection_allowed": True,
                "fit_allowed": True,
                "calibration_allowed": False,
                "evaluation_claim": "development only",
            }
        },
        "locks": {
            "selection_data_end": "2026-02-28",
            "lockbox_start": "2026-03-01",
            "mean_frozen_after": "2026-02-28",
            "uncertainty_frozen_after": "2026-05-31",
            "bundle_frozen_before": "2026-06-01",
            "production_not_before": "2026-09-01",
        },
    }


def test_calendar_expands_the_pinned_fold_phase(tmp_path):
    (tmp_path / "calendar.json").write_text(json.dumps(_calendar()))
    result = ProgramCalendar(
        "calendar", {"path": "calendar.json", "phase": "model_zoo"}
    ).run(_context(tmp_path), {})
    assert result["phase"]["walkforward"]["count"] == 20
    assert result["phase"]["walkforward"]["last_validation_end_exclusive"] == "2025-10-17"
    assert len(result["calendar_sha256"]) == 64


def test_calendar_refuses_a_fold_end_not_implied_by_its_geometry(tmp_path):
    (tmp_path / "calendar.json").write_text(json.dumps(_calendar("2025-10-18")))
    with pytest.raises(ValueError, match="must be 2025-10-17"):
        ProgramCalendar(
            "calendar", {"path": "calendar.json", "phase": "model_zoo"}
        ).run(_context(tmp_path), {})


def test_calendar_refuses_selection_inside_the_lockbox(tmp_path):
    calendar = _calendar()
    calendar["phases"]["model_zoo"]["latest_asof"] = "2026-03-01"
    (tmp_path / "calendar.json").write_text(json.dumps(calendar))
    with pytest.raises(ValueError, match="exceeds selection_data_end"):
        ProgramCalendar(
            "calendar", {"path": "calendar.json", "phase": "model_zoo"}
        ).run(_context(tmp_path), {})


def test_calendar_refuses_folds_outside_the_declared_period(tmp_path):
    calendar = _calendar()
    calendar["periods"]["development"]["end"] = "2025-08-31"
    (tmp_path / "calendar.json").write_text(json.dumps(calendar))
    with pytest.raises(ValueError, match="fold schedule extends outside"):
        ProgramCalendar(
            "calendar", {"path": "calendar.json", "phase": "model_zoo"}
        ).run(_context(tmp_path), {})
