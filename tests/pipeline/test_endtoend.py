"""The end-to-end-config increment: explicit stages, the feature recipe,
$from cross-stage wiring, and tracking sinks."""

import pytest

from dskit.pipeline.base import (
    OPTIMIZER_KINDS,
    ConfigError,
    FeatureConfig,
    FeatureStepConfig,
    OptimizationConfig,
    SinkConfig,
    TrackingConfig,
    is_ref,
    parse_ref,
    register_optimizer_kind,
    resolve_refs,
)
from dskit.pipeline.features import apply_stream_steps
from dskit.pipeline.records import MarketRecord
from dskit.pipeline.runner import Runner
from dskit.pipeline.testing import MemoryTracker


def _rec(contract="SER-EV1-C1", group="SER-EV1", mid=0.4, usable=True, **kw):
    base = dict(
        venue="v",
        instrument="SER",
        contract=contract,
        asof_ms=1_000,
        usable=usable,
        reason="ok" if usable else "no_book",
        group=group,
        mid=mid,
    )
    base.update(kw)
    return MarketRecord(**base)


class TestStagesList:
    def test_derived_defaults_read_from_the_document(self, synthetic_config):
        assert synthetic_config().stages == (
            "train",
            "validate",
            "stat_test",
            "optimize",
            "backtest",
        )
        assert synthetic_config(optimization=None).stages == (
            "train",
            "validate",
            "stat_test",
        )
        # the derived list is concrete in the serialized form
        assert synthetic_config().to_obj()["stages"][-1] == "backtest"

    def test_explicit_short_list_round_trips(self, synthetic_config):
        cfg = synthetic_config(stages=("train", "validate", "stat_test"))
        assert cfg.from_obj(cfg.to_obj()) == cfg

    def test_unknown_duplicate_and_empty_refused(self, synthetic_config):
        with pytest.raises(ConfigError, match="unknown stage"):
            synthetic_config(stages=("train", "deploy"))
        with pytest.raises(ConfigError, match="repeat"):
            synthetic_config(stages=("train", "train", "validate"))
        with pytest.raises(ConfigError, match="non-empty"):
            synthetic_config(stages=())

    def test_gates_are_ordering_not_convention(self, synthetic_config):
        # capital cannot precede the stat test, whatever the config says
        with pytest.raises(ConfigError, match="requires"):
            synthetic_config(
                stages=("train", "validate", "optimize", "stat_test", "backtest")
            )
        with pytest.raises(ConfigError, match="requires"):
            synthetic_config(stages=("validate",))  # scoring needs a signal

    def test_capital_stages_need_an_optimizer(self, synthetic_config):
        with pytest.raises(ConfigError, match="predict-only"):
            synthetic_config(
                optimization=None,
                stages=("train", "validate", "stat_test", "optimize"),
            )


class TestFeatureConfig:
    def test_registered_stream_kinds_validate_params(self):
        with pytest.raises(ConfigError, match="features.filter.*unknown param"):
            FeatureStepConfig(kind="filter", params={"wat": 1})
        with pytest.raises(ConfigError, match="where\\[0\\].field"):
            FeatureStepConfig(
                kind="filter",
                params={"where": [{"field": "vibe", "op": "==", "value": 1}]},
            )
        with pytest.raises(ConfigError, match="coarser clusters only"):
            FeatureStepConfig(kind="regroup", params={"by": "record"})

    def test_empty_steps_refused_and_round_trip(self):
        with pytest.raises(ConfigError, match="non-empty"):
            FeatureConfig(steps=())
        cfg = FeatureConfig(
            steps=(FeatureStepConfig(kind="regroup", params={"by": "instrument"}),)
        )
        assert FeatureConfig.from_obj(cfg.to_obj()) == cfg

    def test_filter_applies_declared_clauses(self):
        records = [
            _rec(contract="A", mid=0.2),
            _rec(contract="B", mid=0.7),
            _rec(contract="C", mid=None),  # None satisfies no comparison
            _rec(contract="D", mid=0.8, usable=False),
        ]
        cfg = FeatureConfig(
            steps=(
                FeatureStepConfig(
                    kind="filter",
                    params={"where": [{"field": "mid", "op": ">", "value": 0.5}]},
                ),
            )
        )
        kept = [r.contract for r in apply_stream_steps(iter(records), cfg)]
        assert kept == ["B"]  # A below, C quote-less, D unusable

    def test_regroup_coarsens_the_cluster(self):
        cfg = FeatureConfig(
            steps=(FeatureStepConfig(kind="regroup", params={"by": "instrument"}),)
        )
        out = list(apply_stream_steps(iter([_rec()]), cfg))
        assert out[0].cluster == "SER"

    def test_unclaimed_backend_kind_fails_at_resolve(self, registry, synthetic_config):
        from dskit.pipeline.resolve import resolve

        cfg = synthetic_config(
            features=FeatureConfig(steps=(FeatureStepConfig(kind="ladder-tensor-v1"),))
        )
        with pytest.raises(ValueError, match="ladder-tensor-v1.*claimed"):
            resolve(cfg, asof="2026-01-01", registry=registry)


class TestRefs:
    def test_grammar(self):
        assert is_ref({"$from": "train.signal"})
        assert not is_ref({"$from": "x", "extra": 1})
        assert parse_ref({"$from": "stat_test.survivors"}) == (
            "stat_test",
            ["survivors"],
        )
        with pytest.raises(ConfigError, match="output-path"):
            parse_ref({"$from": "train"})

    def test_undeclared_stage_targets_caught_at_the_whole_config(
        self, synthetic_config
    ):
        # membership is a whole-document fact: the stage list lives on the
        # PipelineConfig, so the cross-check happens there (custom stage
        # names included), not at parse_ref
        with pytest.raises(ConfigError, match="undeclared stage.*warmup"):
            synthetic_config(
                optimization=OptimizationConfig(
                    kind="mean-variance", params={"x": {"$from": "warmup.y"}}
                )
            )

    def test_resolution_and_dangling_wires(self):
        outputs = {"stat_test": {"survivors": ["A"], "alpha": 0.05}}
        params = {
            "keep": 1,
            "who": {"$from": "stat_test.survivors"},
            "nested": {"a": {"$from": "stat_test.alpha"}},
        }
        resolved = resolve_refs(params, outputs)
        assert resolved == {"keep": 1, "who": ["A"], "nested": {"a": 0.05}}
        with pytest.raises(ValueError, match="has not produced"):
            resolve_refs({"x": {"$from": "train.signal"}}, outputs)
        with pytest.raises(ValueError, match="no output 'ghost'"):
            resolve_refs({"x": {"$from": "stat_test.ghost"}}, outputs)


@pytest.fixture
def wired_kind():
    register_optimizer_kind("wired", lambda params: [])
    try:
        yield "wired"
    finally:
        OPTIMIZER_KINDS.pop("wired", None)


class TestRunnerWiring:
    def test_from_refs_reach_the_backend_resolved(
        self, registry, synthetic_config, wired_kind
    ):
        from dskit.pipeline.testing import SyntheticBackend

        seen = {}

        class Wired(SyntheticBackend):
            supported_optimizer_kinds = ("wired",)

            def optimize(self, ctx, signal, survivors):
                seen["params"] = ctx.optimizer_params
                return {"ok": 1}

            def backtest(self, ctx, signal, survivors):
                return {"ok": 1}

        cfg = synthetic_config(
            optimization=OptimizationConfig(
                kind="wired",
                params={"whom": {"$from": "stat_test.survivors"}, "cap": 9},
            )
        )
        result = Runner(cfg, asof="2026-01-01", backend=Wired(cfg)).run()
        assert seen["params"]["cap"] == 9
        assert seen["params"]["whom"] == list(result.survivors)  # wire resolved

    def test_stream_features_change_what_every_stage_sees(
        self, registry, synthetic_config
    ):
        # cluster = instrument -> 1 event per instrument -> below the floor:
        # the declared recipe visibly drives the gates
        cfg = synthetic_config(
            features=FeatureConfig(
                steps=(FeatureStepConfig(kind="regroup", params={"by": "instrument"}),)
            )
        )
        result = Runner(cfg, asof="2026-01-01", registry=registry).run()
        assert result.halted_after == "validate"
        rows = result.stages[-1].payload["instruments"]
        assert all(r["n_events"] == 1 for r in rows.values())

    def test_tracking_sinks_receive_params_and_stage_metrics(
        self, registry, synthetic_config
    ):
        cfg = synthetic_config(
            tracking=TrackingConfig(sinks=(SinkConfig(kind="memory"),))
        )
        result = Runner(cfg, asof="2026-01-01", registry=registry).run()
        sink = MemoryTracker.instances[-1]
        assert sink.logged_params["pipeline_hash"] == result.pipeline_hash
        assert sink.logged_params["venue"] == "synthetic"
        assert [s for s, _ in sink.metrics] == list(cfg.stages)
        validate_metrics = dict(sink.metrics)["validate"]
        assert "instruments.SYN-A.n_events" in validate_metrics
        assert sink.closed  # closed even though nothing failed

    def test_unregistered_sink_kind_fails_at_resolve(self, registry, synthetic_config):
        cfg = synthetic_config(
            tracking=TrackingConfig(sinks=(SinkConfig(kind="wandb"),))
        )
        with pytest.raises(ValueError, match="wandb.*not registered"):
            Runner(cfg, asof="2026-01-01", registry=registry)


class TestFilterValidatorEdges:
    def test_every_malformation_is_named(self):
        with pytest.raises(ConfigError, match="require_usable must be a bool"):
            FeatureStepConfig(kind="filter", params={"require_usable": 1})
        with pytest.raises(ConfigError, match="where must be a list"):
            FeatureStepConfig(kind="filter", params={"where": {"field": "mid"}})
        with pytest.raises(ConfigError, match="exactly"):
            FeatureStepConfig(kind="filter", params={"where": [{"field": "mid"}]})
        with pytest.raises(ConfigError, match="op '~'"):
            FeatureStepConfig(
                kind="filter",
                params={"where": [{"field": "mid", "op": "~", "value": 1}]},
            )
        with pytest.raises(ConfigError, match="unknown param"):
            FeatureStepConfig(kind="regroup", params={"by": "contract", "x": 1})

    def test_null_tests_and_type_mismatches(self):
        cfg = FeatureConfig(
            steps=(
                FeatureStepConfig(
                    kind="filter",
                    params={"where": [{"field": "mid", "op": "==", "value": None}]},
                ),
            )
        )
        recs = [_rec(contract="A", mid=None), _rec(contract="B", mid=0.4)]
        assert [r.contract for r in apply_stream_steps(iter(recs), cfg)] == ["A"]
        mismatch = FeatureConfig(
            steps=(
                FeatureStepConfig(
                    kind="filter",
                    params={"where": [{"field": "mid", "op": ">", "value": "x"}]},
                ),
            )
        )
        # a type mismatch keeps nothing rather than guessing an ordering
        assert list(apply_stream_steps(iter(recs), mismatch)) == []
