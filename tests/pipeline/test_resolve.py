"""ResolvedPipeline — pure resolution, hash identity, run-dir discipline."""

import json
import os

import pytest

from dskit.pipeline.base import (
    ModelConfig,
    OptimizationConfig,
    RandomSplitConfig,
    StatTestConfig,
    ValidationConfig,
)
from dskit.pipeline.resolve import (
    ResolvedPipeline,
    pipeline_hash,
    resolve,
    write_run_dir,
)


class TestResolution:
    def test_resolves_and_returns_backend(self, registry, synthetic_config):
        resolved, backend = resolve(
            synthetic_config(), asof="2026-01-01", registry=registry
        )
        assert resolved.instruments == ("SYN-A", "SYN-B", "SYN-C")  # discovered
        assert resolved.data_fingerprint["SYN-A"]["events"] == 60
        assert backend.venue == "synthetic"
        assert os.path.isabs(resolved.data_root)

    def test_explicit_instruments_canonicalized(self, registry, synthetic_config):
        cfg = synthetic_config()
        cfg = cfg.from_obj(
            {**cfg.to_obj(), "data": {**cfg.data.to_obj(), "instruments": ["B", "A"]}}
        )
        resolved, _ = resolve(cfg, asof="2026-01-01", registry=registry)
        assert resolved.instruments == ("A", "B")

    def test_purity_same_inputs_same_hash(self, registry, synthetic_config):
        r1, _ = resolve(synthetic_config(), asof="2026-01-01", registry=registry)
        r2, _ = resolve(synthetic_config(), asof="2026-01-01", registry=registry)
        assert pipeline_hash(r1) == pipeline_hash(r2)
        assert r1.run_dir == r2.run_dir

    def test_provenance_excluded_notes_stripped(self, registry, synthetic_config):
        r1, _ = resolve(synthetic_config(), asof="2026-01-01", registry=registry)
        r2, _ = resolve(synthetic_config(), asof="2026-06-30", registry=registry)
        assert pipeline_hash(r1) == pipeline_hash(r2)  # asof is provenance
        noted, _ = resolve(
            synthetic_config(notes="a comment"), asof="2026-01-01", registry=registry
        )
        assert pipeline_hash(noted) == pipeline_hash(r1)

    def test_data_change_changes_identity(self, registry, synthetic_config, tmp_path):
        base, _ = resolve(synthetic_config(), asof="2026-01-01", registry=registry)
        grown = synthetic_config(
            model=ModelConfig(
                name="true-q",
                model_dir=str(tmp_path / "models"),
                params={"n_events": 61},  # one more event -> new fingerprint
            )
        )
        r2, _ = resolve(grown, asof="2026-01-01", registry=registry)
        assert pipeline_hash(base) != pipeline_hash(r2)

    def test_run_dir_embeds_hash8_but_is_not_hashed(self, registry, synthetic_config):
        resolved, _ = resolve(synthetic_config(), asof="2026-01-01", registry=registry)
        h8 = pipeline_hash(resolved)[:8]
        assert resolved.run_dir.endswith(f"syn-2026-01-01-{h8}")
        assert "pipeline_runs" in resolved.run_dir

    def test_rehash_from_written_document(self, registry, synthetic_config):
        resolved, _ = resolve(synthetic_config(), asof="2026-01-01", registry=registry)
        run_dir = write_run_dir(resolved)
        with open(os.path.join(run_dir, "resolved.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        assert pipeline_hash(doc) == doc["pipeline_hash"] == pipeline_hash(resolved)

    def test_missing_data_dir_fails(self, registry, synthetic_config, tmp_path):
        cfg = synthetic_config()
        cfg = cfg.from_obj(
            {
                **cfg.to_obj(),
                "data": {**cfg.data.to_obj(), "data_dir": str(tmp_path / "gone")},
            }
        )
        with pytest.raises(FileNotFoundError):
            resolve(cfg, asof="2026-01-01", registry=registry)

    def test_bad_asof(self, registry, synthetic_config):
        with pytest.raises(ValueError, match="asof"):
            resolve(synthetic_config(), asof="yesterday", registry=registry)


class TestCapabilityChecks:
    def test_unknown_metric_fails_at_resolve(self, registry, synthetic_config):
        cfg = synthetic_config(validation=ValidationConfig(metric="sharpe"))
        with pytest.raises(ValueError, match="metric.*sharpe"):
            resolve(cfg, asof="2026-01-01", registry=registry)

    def test_unknown_correction_fails_at_resolve(self, registry, synthetic_config):
        cfg = synthetic_config(stat_test=StatTestConfig(correction="tukey"))
        with pytest.raises(ValueError, match="correction.*tukey"):
            resolve(cfg, asof="2026-01-01", registry=registry)

    def test_backend_refuses_unsupported_split_kind(self, registry, synthetic_config):
        class TimeOnly:
            venue = "synthetic"
            supported_split_kinds = ("time",)
            supported_optimizer_kinds = ("equal-weight",)

        cfg = synthetic_config(splits=RandomSplitConfig(train_frac=0.6, val_frac=0.2))
        with pytest.raises(ValueError, match="doctrine"):
            resolve(cfg, asof="2026-01-01", backend=TimeOnly())

    def test_backend_refuses_unsupported_optimizer_kind(
        self, registry, synthetic_config
    ):
        cfg = synthetic_config(optimization=OptimizationConfig(kind="mean-variance"))
        with pytest.raises(ValueError, match="optimizer kind 'mean-variance'"):
            resolve(cfg, asof="2026-01-01", registry=registry)

    def test_unregistered_optimizer_kind_fails_even_if_supported(
        self, registry, synthetic_config
    ):
        class Claims:
            venue = "synthetic"
            supported_split_kinds = ("time",)
            supported_optimizer_kinds = ("ghost-kind",)

            def discover_instruments(self, root):
                return ("A",)

            def fingerprint(self, root, instruments, asof):
                return {}

        cfg = synthetic_config(optimization=OptimizationConfig(kind="ghost-kind"))
        with pytest.raises(ValueError, match="no registered validator"):
            resolve(cfg, asof="2026-01-01", backend=Claims())


class TestRunDir:
    def test_write_and_refuse_overwrite(self, registry, synthetic_config):
        resolved, _ = resolve(synthetic_config(), asof="2026-01-01", registry=registry)
        run_dir = write_run_dir(resolved)
        assert os.path.exists(os.path.join(run_dir, "config.json"))
        with pytest.raises(ValueError, match="refusing to overwrite"):
            write_run_dir(resolved)

    def test_resolved_pipeline_shape_rules(self, synthetic_config):
        cfg = synthetic_config()
        with pytest.raises(ValueError, match="sorted"):
            ResolvedPipeline(
                config=cfg,
                asof="2026-01-01",
                data_root="/d",
                model_root="/m",
                instruments=("B", "A"),
                run_dir="/r",
            )
        with pytest.raises(ValueError, match="non-empty tuple"):
            ResolvedPipeline(
                config=cfg,
                asof="2026-01-01",
                data_root="/d",
                model_root="/m",
                instruments=(),
                run_dir="/r",
            )
        with pytest.raises(ValueError, match="asof"):
            ResolvedPipeline(
                config=cfg,
                asof="not-a-date",
                data_root="/d",
                model_root="/m",
                instruments=("A",),
                run_dir="/r",
            )
        with pytest.raises(ValueError, match="PipelineConfig"):
            ResolvedPipeline(
                config={},
                asof="2026-01-01",
                data_root="/d",
                model_root="/m",
                instruments=("A",),
                run_dir="/r",
            )
