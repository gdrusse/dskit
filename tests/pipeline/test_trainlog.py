"""TrainingCurve (ADR-0025): append rules, best(), round-trip."""

from __future__ import annotations

import pytest

from dskit.pipeline.trainlog import TrainingCurve


def curve_of(rows):
    curve = TrainingCurve()
    for epoch, metrics in rows:
        curve.record(epoch, metrics)
    return curve


def test_records_rows_in_order_and_exposes_copies():
    curve = curve_of([(0, {"train_loss": 1.0}), (1, {"train_loss": 0.5})])
    assert len(curve) == 2
    rows = curve.rows
    assert rows == (
        {"epoch": 0, "train_loss": 1.0},
        {"epoch": 1, "train_loss": 0.5},
    )
    rows[0]["train_loss"] = 99.0  # a copy — the curve stays append-only
    assert curve.rows[0]["train_loss"] == 1.0


@pytest.mark.parametrize("epoch", [-1, 1.5, True, "0"])
def test_epoch_must_be_a_plain_int(epoch):
    with pytest.raises(ValueError, match="epoch"):
        TrainingCurve().record(epoch, {"loss": 1.0})


def test_epochs_are_strictly_ordered():
    curve = curve_of([(3, {"loss": 1.0})])
    with pytest.raises(ValueError, match="strictly ordered"):
        curve.record(3, {"loss": 0.9})
    with pytest.raises(ValueError, match="strictly ordered"):
        curve.record(1, {"loss": 0.9})
    curve.record(4, {"loss": 0.8})  # forward is fine, gaps included


@pytest.mark.parametrize(
    "metrics",
    [
        {},
        "loss",
        {"loss": float("nan")},
        {"loss": float("inf")},
        {"loss": "low"},
        {"loss": True},
        {"": 1.0},
        {"epoch": 1.0},
        {7: 1.0},
    ],
)
def test_metric_rows_are_validated_at_append(metrics):
    with pytest.raises(ValueError):
        TrainingCurve().record(0, metrics)


def test_best_min_max_and_first_epoch_tie_break():
    curve = curve_of(
        [
            (0, {"val_loss": 0.5, "acc": 0.1}),
            (1, {"val_loss": 0.3, "acc": 0.9}),
            (2, {"val_loss": 0.3, "acc": 0.9}),  # tie -> earliest wins
        ]
    )
    assert curve.best("val_loss") == (1, 0.3)
    assert curve.best("acc", select="max") == (1, 0.9)


def test_best_refusals_name_the_problem():
    with pytest.raises(ValueError, match="no epochs"):
        TrainingCurve().best("loss")
    curve = curve_of([(0, {"loss": 1.0}), (1, {"other": 2.0})])
    with pytest.raises(ValueError, match="partial coverage"):
        curve.best("loss")
    with pytest.raises(ValueError, match="select"):
        curve_of([(0, {"loss": 1.0})]).best("loss", select="best")


def test_round_trip_and_default_deny_envelope():
    curve = curve_of([(0, {"train_loss": 1.0, "val_loss": 2.0})])
    restored = TrainingCurve.from_obj(curve.to_obj())
    assert restored.rows == curve.rows
    with pytest.raises(ValueError, match="unknown key"):
        TrainingCurve.from_obj({"epochs": [], "notes": "x"})
    with pytest.raises(ValueError, match="dict"):
        TrainingCurve.from_obj([1])
    with pytest.raises(ValueError, match="epoch"):
        TrainingCurve.from_obj({"epochs": [{"loss": 1.0}]})
    # A hand-edited artifact fails the same way a live append would.
    with pytest.raises(ValueError, match="finite"):
        TrainingCurve.from_obj({"epochs": [{"epoch": 0, "loss": float("inf")}]})
