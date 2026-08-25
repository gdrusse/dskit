"""dskit/pipeline/trainlog.py + the per-epoch telemetry it gives the
torch pack (requirement 7: watch the loss while the model trains).

What these lock down:

* the probability metrics are the ones the toolkit ALREADY owns
  (``logloss``/``brier`` from ``dskit.pipeline.metrics``) plus the ECE
  reduction of ``validate``'s own reliability buckets — not a new zoo;
* a fit with ``val_rows`` wired records a VALIDATION loss every epoch,
  streams it as it goes, and lands the complete curve in a durable
  artifact even when the stream is strided;
* the stream is BOUNDED — a long fit costs ~``max_log_lines`` lines, not
  one per epoch — because a 90-market run has to stay readable.
"""

from __future__ import annotations

import json
import logging
import os

import pytest

from dskit.pipeline.trainlog import (
    TrainingCurve,
    is_binary,
    probability_metrics,
)

torch = pytest.importorskip("torch")

from dskit.pipeline.libs.torch import LinearRegressor  # noqa: E402


class _Recorder(logging.Handler):
    """Captures the streamed lines so a test can count and read them."""

    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def _logger_with(recorder, name):
    log = logging.getLogger(name)
    log.setLevel(logging.INFO)
    log.addHandler(recorder)
    log.propagate = False
    return log


# ---------------------------------------------------------------------------
# probability_metrics
# ---------------------------------------------------------------------------


class TestProbabilityMetrics:
    def test_perfect_calibration_scores_zero_ece(self):
        preds = [0.0, 0.0, 1.0, 1.0]
        labels = [0.0, 0.0, 1.0, 1.0]
        out = probability_metrics(preds, labels)
        assert out["ece"] == pytest.approx(0.0)
        assert out["brier"] == pytest.approx(0.0)
        assert out["n"] == 4

    def test_ece_catches_a_confidently_wrong_model(self):
        # Every belief 0.9, every outcome 0 — maximally miscalibrated.
        out = probability_metrics([0.9] * 10, [0.0] * 10)
        assert out["ece"] == pytest.approx(0.9)
        assert out["brier"] == pytest.approx(0.81)

    def test_it_reuses_the_toolkits_own_metrics(self):
        from dskit.pipeline.metrics import brier, logloss

        preds, labels = [0.3, 0.8], [0.0, 1.0]
        out = probability_metrics(preds, labels)
        expected_ll = (logloss(0.3, 0.0) + logloss(0.8, 1.0)) / 2
        expected_br = (brier(0.3, 0.0) + brier(0.8, 1.0)) / 2
        assert out["logloss"] == pytest.approx(expected_ll)
        assert out["brier"] == pytest.approx(expected_br)

    def test_non_binary_labels_get_no_fabricated_probability_scores(self):
        assert probability_metrics([0.2, 0.4], [1.5, 2.5]) == {}
        assert not is_binary([1.5])

    def test_a_diverged_head_reports_nothing_rather_than_crashing(self):
        assert probability_metrics([float("nan"), 0.5], [1.0, 0.0]) == {}

    def test_out_of_range_beliefs_are_clamped_not_refused(self):
        # A mid-training head can overshoot; the metric must still report.
        out = probability_metrics([1.4, -0.3], [1.0, 0.0])
        assert out["brier"] == pytest.approx(0.0)

    def test_mismatched_lengths_refuse(self):
        with pytest.raises(ValueError, match="they must pair"):
            probability_metrics([0.5], [1.0, 0.0])


# ---------------------------------------------------------------------------
# TrainingCurve
# ---------------------------------------------------------------------------


class TestTrainingCurve:
    def test_it_tracks_the_best_epoch_on_the_declared_objective(self):
        rec = _Recorder()
        curve = TrainingCurve(
            "qhat", _logger_with(rec, "test.curve.best"), total_epochs=3
        )
        curve.record(1, 0.9, val_loss=0.8)
        curve.record(2, 0.5, val_loss=0.4)
        curve.record(3, 0.4, val_loss=0.6)
        assert curve.best_epoch == 2
        assert curve.summary()["best_val_loss"] == pytest.approx(0.4)

    def test_the_stream_is_bounded_for_a_long_fit(self):
        rec = _Recorder()
        curve = TrainingCurve(
            "qhat",
            _logger_with(rec, "test.curve.bounded"),
            total_epochs=200,
            max_lines=10,
        )
        for epoch in range(1, 201):
            # A flat, never-improving curve: only the stride can stream.
            curve.record(epoch, 1.0, val_loss=1.0)
        assert len(rec.lines) <= 12, rec.lines
        # ...but the ARTIFACT keeps every epoch.
        assert curve.payload()["epochs_run"] == 200
        assert len(curve.payload()["epochs"]) == 200

    def test_the_first_and_last_epoch_always_stream(self):
        rec = _Recorder()
        curve = TrainingCurve(
            "qhat",
            _logger_with(rec, "test.curve.ends"),
            total_epochs=50,
            max_lines=3,
        )
        for epoch in range(1, 51):
            curve.record(epoch, 1.0)
        assert any("epoch 1/50" in line for line in rec.lines)
        assert any("epoch 50/50" in line for line in rec.lines)

    def test_non_finite_values_serialize_as_null(self):
        rec = _Recorder()
        curve = TrainingCurve(
            "qhat", _logger_with(rec, "test.curve.nan"), total_epochs=1
        )
        curve.record(1, float("nan"), val_loss=float("inf"))
        # write_artifact refuses NaN — the payload must already be clean.
        json.dumps(curve.payload(), allow_nan=False)


# ---------------------------------------------------------------------------
# TorchTrain wiring
# ---------------------------------------------------------------------------


def _rows(n, *, binary):
    out = []
    for i in range(n):
        x = i / n
        label = float(i % 2) if binary else x * 2.0
        out.append({"x": x, "label": label})
    return out


class _Ctx:
    def __init__(self, run_dir):
        self.run_dir = run_dir


class TestTorchTrainTelemetry:
    def _node(self, tmp_path, params, recorder, name):
        node = LinearRegressor(
            key="qhat",
            params={"features": ["x"], "epochs": 4, **params},
        )
        node.log = _logger_with(recorder, name)
        return node

    def test_a_val_port_produces_per_epoch_validation_loss(self, tmp_path):
        rec = _Recorder()
        node = self._node(tmp_path, {}, rec, "test.torch.val")
        out = node.run(
            _Ctx(str(tmp_path)),
            {"rows": _rows(40, binary=True), "val_rows": _rows(20, binary=True)},
        )
        curve = json.load(
            open(os.path.join(tmp_path, "artifacts", "qhat", "training_curve.json"))
        )
        assert curve["epochs_run"] == 4
        for row in curve["epochs"]:
            assert "train_loss" in row
            assert "val_loss" in row
            # The calibrated-probability metrics ride along on binary labels.
            assert "logloss" in row and "brier" in row and "ece" in row
        assert out["metrics"]["n_val_rows"] == 20
        assert "best_val_loss" in out["metrics"]
        # ...and it was VISIBLE while it ran, not only at the end.
        assert any("epoch 1/4" in line for line in rec.lines)

    def test_without_a_val_port_the_curve_tracks_train_loss(self, tmp_path):
        rec = _Recorder()
        node = self._node(tmp_path, {}, rec, "test.torch.noval")
        out = node.run(_Ctx(str(tmp_path)), {"rows": _rows(40, binary=True)})
        assert out["metrics"]["objective"] == "train_loss"
        curve = json.load(
            open(os.path.join(tmp_path, "artifacts", "qhat", "training_curve.json"))
        )
        assert all("val_loss" not in row for row in curve["epochs"])

    def test_a_regression_target_gets_losses_and_no_probability_scores(self, tmp_path):
        rec = _Recorder()
        node = self._node(tmp_path, {}, rec, "test.torch.reg")
        node.run(
            _Ctx(str(tmp_path)),
            {"rows": _rows(40, binary=False), "val_rows": _rows(20, binary=False)},
        )
        curve = json.load(
            open(os.path.join(tmp_path, "artifacts", "qhat", "training_curve.json"))
        )
        for row in curve["epochs"]:
            assert "val_loss" in row
            assert "logloss" not in row  # never fabricated on a continuous target

    def test_a_miswired_val_port_refuses_rather_than_training_blind(self, tmp_path):
        rec = _Recorder()
        node = self._node(tmp_path, {}, rec, "test.torch.miswired")
        with pytest.raises(ValueError, match="training blind"):
            node.run(
                _Ctx(str(tmp_path)),
                {
                    "rows": _rows(40, binary=True),
                    "val_rows": [{"wrong_key": 1.0, "label": 1.0}],
                },
            )

    def test_the_verbosity_knobs_validate(self):
        assert LinearRegressor.validate_params({"features": ["x"], "log_every": 0}), (
            "log_every=0 must be refused"
        )
        assert not LinearRegressor.validate_params(
            {"features": ["x"], "log_every": 5, "max_log_lines": 0}
        )

    def test_determinism_is_unchanged_by_the_telemetry(self, tmp_path):
        """One seed still means one state dict — the curve must not consume
        RNG (the artifact's identity rests on this)."""
        digests = []
        for i in range(2):
            rec = _Recorder()
            run_dir = tmp_path / f"run{i}"
            node = self._node(run_dir, {}, rec, f"test.torch.det{i}")
            out = node.run(
                _Ctx(str(run_dir)),
                {"rows": _rows(40, binary=True), "val_rows": _rows(20, binary=True)},
            )
            sidecar = json.load(open(out["artifact_path"].replace(".pt", ".json")))
            digests.append(sidecar["state_hash"])
        assert digests[0] == digests[1]
