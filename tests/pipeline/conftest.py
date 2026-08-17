"""Shared fixtures: a private registry with the synthetic venue bound."""

import pytest

from dskit.pipeline.base import (
    DataConfig,
    ModelConfig,
    OptimizationConfig,
    PipelineConfig,
    TimeSplitConfig,
    ValidationConfig,
)
from dskit.pipeline.registry import BackendRegistry
from dskit.pipeline.testing import (
    TEST_END_MS,
    TRAIN_END_MS,
    VAL_END_MS,
    register_synthetic,
)


@pytest.fixture
def registry():
    """A private registry with the synthetic venue (and its optimizer kind,
    which registers into the global OPTIMIZER_KINDS idempotently)."""
    reg = BackendRegistry()
    register_synthetic(reg)
    return reg


@pytest.fixture
def synthetic_config(tmp_path):
    """A complete synthetic-venue config over a real (empty) tmp data dir."""

    def build(**overrides):
        base = dict(
            name="syn",
            data=DataConfig(venue="synthetic", data_dir=str(tmp_path)),
            splits=TimeSplitConfig(
                train_end_ms=TRAIN_END_MS,
                val_end_ms=VAL_END_MS,
                test_end_ms=TEST_END_MS,
            ),
            model=ModelConfig(
                name="true-q",
                model_dir=str(tmp_path / "models"),
                params={"n_events": 60},
            ),
            validation=ValidationConfig(min_events=10),
            optimization=OptimizationConfig(kind="equal-weight"),
        )
        base.update(overrides)
        return PipelineConfig(**base)

    return build
