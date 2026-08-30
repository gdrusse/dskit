"""Recurring acquisition delegates every iteration to the finite pull."""

import pytest

from dskit.assets.base import AssetError
from dskit.onboarding import watch as watch_module
from dskit.onboarding.watch import run_watch


def test_watch_runs_finite_acquisitions_at_declared_interval(
        root, registry, monkeypatch):
    calls = []
    sleeps = []
    results = []

    def acquire(*args, **kwargs):
        calls.append((args, kwargs))
        return {"records": len(calls), "snapshot": f"s{len(calls)}"}

    monkeypatch.setattr(watch_module, "run_acquisition", acquire)
    summary = run_watch(
        root, registry, "vendor", "bars", "live", 60,
        sleep=sleeps.append, max_iterations=3, on_result=results.append,
    )

    assert len(calls) == 3
    assert all(call[0][2:6] == ("vendor", "bars", "live", "watch")
               for call in calls)
    assert sleeps == [60, 60]
    assert results == [
        {"records": 1, "snapshot": "s1"},
        {"records": 2, "snapshot": "s2"},
        {"records": 3, "snapshot": "s3"},
    ]
    assert summary == {"iterations": 3, "last": results[-1]}


def test_watch_stops_on_first_error(root, registry, monkeypatch):
    calls = []

    def acquire(*args, **kwargs):
        calls.append(args)
        if len(calls) == 2:
            raise AssetError(["gap-visible failure"])
        return {"records": 0, "snapshot": None}

    monkeypatch.setattr(watch_module, "run_acquisition", acquire)
    with pytest.raises(AssetError, match="gap-visible failure"):
        run_watch(
            root, registry, "vendor", "bars", "live", 1,
            sleep=lambda seconds: None, max_iterations=5,
        )
    assert len(calls) == 2


def test_empty_pull_is_an_honest_iteration(root, registry, monkeypatch):
    calls = []
    monkeypatch.setattr(
        watch_module,
        "run_acquisition",
        lambda *args, **kwargs: calls.append(args) or {
            "records": 0, "snapshot": None,
        },
    )
    summary = run_watch(
        root, registry, "vendor", "bars", "live", 1,
        sleep=lambda seconds: None, max_iterations=2,
    )
    assert len(calls) == 2
    assert summary["iterations"] == 2
    assert summary["last"]["snapshot"] is None


@pytest.mark.parametrize("seconds", [0, -1, True, "60"])
def test_interval_must_be_a_positive_number(root, registry, seconds):
    with pytest.raises(AssetError, match="every_seconds"):
        run_watch(
            root, registry, "vendor", "bars", "live", seconds,
            max_iterations=1,
        )


@pytest.mark.parametrize("iterations", [0, -1, True, 1.5])
def test_test_bound_must_be_a_positive_integer(root, registry, iterations):
    with pytest.raises(AssetError, match="max_iterations"):
        run_watch(
            root, registry, "vendor", "bars", "live", 60,
            max_iterations=iterations,
        )
