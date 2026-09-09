"""Release rotation calendar tests."""

from datetime import datetime, timedelta, timezone
import json

import pytest

from dskit.pipeline.release_rotation import (
    HalfOpenInterval,
    ReleaseRotationCalendar,
    ReleaseRotationWindow,
)
ANCHOR = datetime(2026, 1, 8, 15, tzinfo=timezone.utc)


def _calendar(**overrides):
    values = {
        "anchor": ANCHOR,
        "cadence": timedelta(days=7),
        "trailing_training_span": timedelta(days=5),
        "embargo": timedelta(days=1),
    }
    values.update(overrides)
    return ReleaseRotationCalendar(**values)


def test_materializes_half_open_pinned_windows_and_manifest():
    calendar = _calendar()
    windows = calendar.materialize(
        ANCHOR,
        ANCHOR + timedelta(days=14),
        max_windows=2,
    )

    assert [window.index for window in windows] == [0, 1]
    assert windows[0].training.end == ANCHOR - timedelta(days=1)
    assert windows[0].embargo.start == windows[0].training.end
    assert windows[0].embargo.end == ANCHOR
    assert windows[0].release_at == windows[0].embargo.end
    assert windows[0].id != windows[1].id
    manifest = calendar.manifest(
        ANCHOR,
        ANCHOR + timedelta(days=14),
        max_windows=2,
    )
    assert manifest["calendar_sha256"] == calendar.digest
    assert len(manifest["manifest_sha256"]) == 64
    assert [row["id"] for row in manifest["windows"]] == [window.id for window in windows]
    json.dumps(manifest, allow_nan=False)


def test_identity_is_stable_for_equivalent_aware_offsets():
    eastern = timezone(timedelta(hours=-5))
    equivalent = _calendar(anchor=datetime(2026, 1, 8, 10, tzinfo=eastern))
    calendar = _calendar()

    assert equivalent.digest == calendar.digest
    first = equivalent.materialize(ANCHOR, ANCHOR + timedelta(days=7), max_windows=1)
    second = calendar.materialize(ANCHOR, ANCHOR + timedelta(days=7), max_windows=1)
    assert first[0].id == second[0].id


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"anchor": ANCHOR.replace(tzinfo=None)}, "timezone-aware"),
        ({"cadence": timedelta(0)}, "cadence"),
        ({"trailing_training_span": timedelta(0)}, "trailing_training_span"),
        ({"embargo": timedelta(days=-1)}, "embargo"),
        ({"trailing_training_span": timedelta(days=7), "embargo": timedelta(days=1)}, "overlap"),
        ({"anchor": datetime.min.replace(tzinfo=timezone.utc)}, "underflows"),
    ],
)
def test_refuses_invalid_calendar_values(overrides, message):
    with pytest.raises(ValueError, match=message):
        _calendar(**overrides)


def test_refuses_ambiguous_or_unbounded_materialization():
    calendar = _calendar()
    with pytest.raises(ValueError, match="anchor"):
        calendar.materialize(ANCHOR - timedelta(seconds=1), ANCHOR, max_windows=1)
    with pytest.raises(ValueError, match="after start"):
        calendar.materialize(ANCHOR, ANCHOR, max_windows=1)
    with pytest.raises(ValueError, match="positive int"):
        calendar.materialize(ANCHOR, ANCHOR + timedelta(days=7), max_windows=True)
    with pytest.raises(ValueError, match="exceeds max_windows"):
        calendar.materialize(ANCHOR, ANCHOR + timedelta(days=14), max_windows=1)

def test_zero_embargo_materializes_without_an_empty_interval():
    window = _calendar(embargo=timedelta(0)).materialize(
        ANCHOR,
        ANCHOR + timedelta(days=7),
        max_windows=1,
    )[0]

    assert window.training.end == ANCHOR
    assert window.embargo is None
    assert window.to_obj()["embargo"] is None


@pytest.mark.parametrize("digest", ["A" * 64, "g" * 64, "a" * 63, "a" * 64 + "\n"])
def test_window_refuses_a_noncanonical_calendar_digest(digest):
    training = HalfOpenInterval(ANCHOR - timedelta(days=2), ANCHOR - timedelta(days=1))
    embargo = HalfOpenInterval(ANCHOR - timedelta(days=1), ANCHOR)

    with pytest.raises(ValueError, match="lowercase SHA-256"):
        ReleaseRotationWindow(0, digest, ANCHOR, training, embargo)


@pytest.mark.parametrize("end", [ANCHOR, ANCHOR - timedelta(seconds=1)])
def test_half_open_interval_refuses_equal_or_reversed_bounds(end):
    with pytest.raises(ValueError, match="end must be after start"):
        HalfOpenInterval(ANCHOR, end)


@pytest.mark.parametrize(
    ("training", "embargo", "release_at", "message"),
    [
        (
            HalfOpenInterval(ANCHOR - timedelta(days=3), ANCHOR - timedelta(days=2)),
            HalfOpenInterval(ANCHOR - timedelta(days=1), ANCHOR),
            ANCHOR,
            "intervals must join",
        ),
        (
            HalfOpenInterval(ANCHOR - timedelta(days=3), ANCHOR - timedelta(days=2)),
            HalfOpenInterval(ANCHOR - timedelta(days=2), ANCHOR - timedelta(days=1)),
            ANCHOR,
            "intervals must join",
        ),
        (
            HalfOpenInterval(ANCHOR - timedelta(days=2), ANCHOR - timedelta(days=1)),
            None,
            ANCHOR,
            "zero-embargo",
        ),
    ],
)
def test_window_refuses_mismatched_interval_joins(training, embargo, release_at, message):
    with pytest.raises(ValueError, match=message):
        ReleaseRotationWindow(0, "a" * 64, release_at, training, embargo)
