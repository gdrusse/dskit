"""The Runner — stage order, deploy gates, artifacts, causality guard."""

import json
import os

import pytest

from dskit.pipeline.base import ModelConfig, ValidationConfig
from dskit.pipeline.records import MarketRecord
from dskit.pipeline.runner import Runner


def _stage_names(result):
    return [sr.stage for sr in result.stages]


class TestFullRun:
    def test_informed_model_deploys(self, registry, synthetic_config):
        result = Runner(synthetic_config(), asof="2026-01-01", registry=registry).run()
        assert _stage_names(result) == [
            "train",
            "validate",
            "stat_test",
            "optimize",
            "backtest",
        ]
        assert result.halted_after is None
        assert len(result.survivors) >= 1
        # the mispricing is real, the model knows the truth: profits follow
        assert result.stages[-1].payload["net_pnl"] > 0

    def test_artifacts_written_and_verdict_first(self, registry, synthetic_config):
        result = Runner(synthetic_config(), asof="2026-01-01", registry=registry).run()
        for name in ("config.json", "resolved.json", "result.json", "report.md"):
            assert os.path.exists(os.path.join(result.run_dir, name))
        stage_files = sorted(os.listdir(os.path.join(result.run_dir, "stages")))
        assert stage_files[0] == "01-train.json"
        assert len(stage_files) == 5
        with open(os.path.join(result.run_dir, "report.md"), encoding="utf-8") as fh:
            first_line = fh.readline().strip()
        assert first_line == f"**{result.verdict}**"  # verdict leads, house rule
        with open(os.path.join(result.run_dir, "result.json"), encoding="utf-8") as fh:
            summary = json.load(fh)
        assert summary["pipeline_hash"] == result.pipeline_hash
        assert summary["survivors"] == list(result.survivors)

    def test_rerun_refused_same_identity(self, registry, synthetic_config):
        Runner(synthetic_config(), asof="2026-01-01", registry=registry).run()
        with pytest.raises(ValueError, match="refusing to overwrite"):
            Runner(synthetic_config(), asof="2026-01-01", registry=registry).run()


class TestDeployGates:
    def test_uninformed_model_halts_before_capital(
        self, registry, synthetic_config, tmp_path
    ):
        cfg = synthetic_config(
            model=ModelConfig(
                name="market-copy",
                model_dir=str(tmp_path / "models"),
                params={"n_events": 60},
            )
        )
        result = Runner(cfg, asof="2026-01-01", registry=registry).run()
        assert result.halted_after == "stat_test"
        assert result.survivors == ()
        assert "optimize" not in _stage_names(result)  # no capital stage ran
        assert "NO-GO" in result.verdict

    def test_min_events_floor_halts_at_validate(self, registry, synthetic_config):
        cfg = synthetic_config(validation=ValidationConfig(min_events=10_000))
        result = Runner(cfg, asof="2026-01-01", registry=registry).run()
        assert result.halted_after == "validate"
        assert _stage_names(result) == ["train", "validate"]
        assert "NO-GO" in result.stages[-1].verdict

    def test_predict_only_run_is_a_stage_list_without_capital(
        self, registry, synthetic_config
    ):
        # optimization=None derives stages=(train, validate, stat_test):
        # the config document itself says no capital stage runs
        cfg = synthetic_config(optimization=None)
        assert cfg.stages == ("train", "validate", "stat_test")
        result = Runner(cfg, asof="2026-01-01", registry=registry).run()
        assert result.halted_after is None  # every DECLARED stage ran
        assert len(result.survivors) >= 1  # the edge is real...
        assert "optimize" not in _stage_names(result)  # ...but none was asked for


class TestCausalityGuard:
    def test_unordered_stream_fails_loud(self, registry, synthetic_config):
        class Unordered:
            venue = "synthetic"
            supported_split_kinds = ("time",)
            supported_optimizer_kinds = ("equal-weight",)

            def discover_instruments(self, root):
                return ("X",)

            def fingerprint(self, root, instruments, asof):
                return {}

            def data_source(self, ctx):
                class Src:
                    def records_for(self, instrument, start_ms, end_ms):
                        for ts in (2_000, 1_000):  # descending: leak risk
                            yield MarketRecord(
                                venue="synthetic",
                                instrument="X",
                                contract=f"X-{ts}",
                                asof_ms=ts,
                                usable=True,
                                reason="ok",
                                mid=0.5,
                            )

                return Src()

            def baseline(self, ctx):
                class B:
                    def q_hat(self, c, p, t, lead_frac=None):
                        return p

                return B()

            def accounting(self, ctx):
                from dskit.pipeline.records import BinaryAccounting

                return BinaryAccounting()

            def settlement(self, ctx):
                class S:
                    def outcomes_for(self, instrument):
                        return {}

                return S()

            def train(self, ctx):
                return self.baseline(ctx)

        with pytest.raises(ValueError, match="non-ascending"):
            Runner(synthetic_config(), asof="2026-01-01", backend=Unordered()).run()
