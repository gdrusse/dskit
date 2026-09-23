"""EmpiricalLocationScale: the first sample-set model rung (ADR-0168)."""

import random

import pytest

from dskit.pipeline.base import ConfigError, TimeSplitConfig
from dskit.pipeline.distribution_models import (
    REFERENCE_SCALE_FIELD,
    EmpiricalLocationScale,
)
from dskit.pipeline.distribution_scores import SampleDistribution
from dskit.pipeline.node import NodeContext

DAY = 24 * 60 * 60 * 1000
PARAMS = {"fit_split": "train", "label": "y", "scale_field": "vol", "n_samples": 50}


def _rows():
    rng = random.Random(9)
    out = []
    for i in range(80):
        vol = 0.01 * (1 + (i % 4))
        out.append({"contract": f"C{i}", "asof_ms": (1 if i < 40 else 15) * DAY + i,
                    "vol": vol, "y": vol * rng.gauss(0, 1) + (0.0 if i < 40 else 5.0)})
    out.append({"contract": "late", "asof_ms": 15 * DAY + 99, "vol": 0.02, "y": None})
    out.append({"contract": "novol", "asof_ms": 15 * DAY + 98, "vol": 0.0, "y": 0.1})
    return out


@pytest.fixture
def ctx(tmp_path):
    splits = TimeSplitConfig(train_end_ms=10 * DAY, val_end_ms=20 * DAY, test_end_ms=30 * DAY)
    return NodeContext(name="m", asof="2026-01-01", run_dir=str(tmp_path),
                       splits=splits, splits_info=splits.to_obj())


def test_fits_on_train_only_and_emits_standardized_forecasts(ctx):
    out = EmpiricalLocationScale("model", dict(PARAMS)).run(ctx, {"rows": _rows()})
    rows = out["rows"]
    assert len(rows) == 82
    shape = rows[0]["samples"]
    assert len(shape) == 50 and shape == sorted(shape)
    # val rows carry a +5 shift the train split never saw: the shape must not
    assert max(shape) < 5
    assert rows[10]["outcome"] == pytest.approx(rows[10]["y"] / rows[10]["vol"])
    assert rows[10][REFERENCE_SCALE_FIELD] == rows[10]["vol"]
    late, novol = rows[-2], rows[-1]
    assert late["outcome"] is None and late["samples"] is not None
    assert novol["samples"] is None and novol[REFERENCE_SCALE_FIELD] is None


def test_scale_multiplier_rescales_outcomes_not_the_standardized_shape(ctx, tmp_path):
    base = EmpiricalLocationScale("a", dict(PARAMS)).run(ctx, {"rows": _rows()})["rows"]
    wide = EmpiricalLocationScale("b", dict(PARAMS, scale_multiplier=2.0)).run(
        ctx, {"rows": _rows()})["rows"]
    assert wide[5]["outcome"] == pytest.approx(base[5]["outcome"] / 2)
    assert wide[5]["samples"] == pytest.approx([q / 2 for q in base[5]["samples"]])


def test_relative_scale_hook_stretches_the_shape(ctx):
    class Doubled(EmpiricalLocationScale):
        def relative_scale(self, row, state):
            return 2.0

    base = EmpiricalLocationScale("a", dict(PARAMS)).run(ctx, {"rows": _rows()})["rows"]
    doubled = Doubled("b", dict(PARAMS)).run(ctx, {"rows": _rows()})["rows"]
    assert doubled[3]["samples"] == pytest.approx([2 * q for q in base[3]["samples"]])


def test_shape_quantiles_track_the_fit_distribution(ctx):
    rows = EmpiricalLocationScale("m", dict(PARAMS, n_samples=200)).run(
        ctx, {"rows": _rows()})["rows"]
    z = [r["y"] / r["vol"] for r in _rows()[:40]]
    dist = SampleDistribution(rows[0]["samples"])
    assert dist.quantile(0.5) == pytest.approx(SampleDistribution(z).quantile(0.5), abs=0.2)


def test_load_mode_restores_the_same_forecasts(ctx, tmp_path):
    out = EmpiricalLocationScale("model", dict(PARAMS)).run(ctx, {"rows": _rows()})
    artifacts = tmp_path / "artifacts" / "model"
    assert any(artifacts.iterdir())
    loaded = EmpiricalLocationScale("model", dict(PARAMS), mode="load",
                                    artifact=str(artifacts)).run(ctx, {"rows": _rows()})
    assert loaded["rows"] == out["rows"]


def test_too_few_usable_fit_rows_refuses(ctx):
    rows = [dict(r, y=None) for r in _rows()]
    with pytest.raises(ValueError, match="need >= 2"):
        EmpiricalLocationScale("m", dict(PARAMS)).run(ctx, {"rows": rows})


@pytest.mark.parametrize("bad", [
    {"label": None}, {"scale_field": ""}, {"scale_multiplier": 0},
    {"scale_multiplier": True}, {"n_samples": 1}, {"typo": 1}, {"fit_split": "nope"},
])
def test_invalid_params_refuse(bad):
    params = {k: v for k, v in dict(PARAMS, **bad).items() if v is not None}
    with pytest.raises(ConfigError):
        EmpiricalLocationScale("m", params)
