"""Imported-component references — 'pkg.module:Attr' anywhere a kind goes,
plus named custom stages: the customization layer over the seams."""

import pytest

from abc import ABC, abstractmethod

from dskit.pipeline.base import (
    ConfigError,
    FeatureConfig,
    FeatureStepConfig,
    ModelConfig,
    OptimizationConfig,
    SinkConfig,
    TrackingConfig,
    import_library_class,
    import_ref,
    is_class_ref,
    parse_stage_entry,
)
from dskit.pipeline.features import apply_stream_steps
from dskit.pipeline.node import Node, node_class_errors
from dskit.pipeline.resolve import resolve
from dskit.pipeline.runner import Runner

HELPERS = "tests.pipeline.refhelpers"


class HalfWrittenNode(Node):
    """A Node subclass that declares a hook and never implements it — the
    shape both doorways must refuse, and the ONE class through which their
    wording is compared."""

    role = "transform"

    @abstractmethod
    def step(self):
        """The hook this class deliberately leaves abstract."""

    def run(self, ctx, inputs):
        return {}


class HalfWrittenPlain(ABC):
    """The same defect on a class that is NOT a Node — what a declared
    library class (a torch adapter, an nn.Module) actually looks like, and
    therefore what the import doorway alone can refuse."""

    @abstractmethod
    def step(self):
        """The hook this class deliberately leaves abstract."""


class TestReferenceGrammar:
    def test_recognition(self):
        assert is_class_ref("mypkg.transforms:VolAdjustedMid")
        assert is_class_ref("a:B")
        assert not is_class_ref("filter")  # registered kind, no colon
        assert not is_class_ref("a.b.c")  # no attr
        assert not is_class_ref("a:b:c")
        assert not is_class_ref(":X")
        assert parse_stage_entry("report=pkg.mod:Cls") == ("report", "pkg.mod:Cls")
        assert parse_stage_entry("train") == ("train", None)

    def test_import_ref_failures_name_the_ref(self):
        with pytest.raises(ValueError, match="cannot import"):
            import_ref("no.such.module:X")
        with pytest.raises(ValueError, match="no attribute"):
            import_ref(f"{HELPERS}:missing_attr")


class TestFeatureStepRefs:
    def test_custom_stream_transform_runs(self, registry, synthetic_config):
        cfg = synthetic_config(
            features=FeatureConfig(
                steps=(
                    FeatureStepConfig(
                        kind=f"{HELPERS}:drop_low_mids", params={"floor": 0.5}
                    ),
                )
            )
        )
        result = Runner(cfg, asof="2026-01-01", registry=registry).run()
        rows = result.stages[1].payload["instruments"]
        # only high-mid (q_true=0.95, mid=0.68) contracts survive the stream
        assert all(0 < r["n_records"] < 20 for r in rows.values())

    def test_validate_params_hook_runs_at_construction(self):
        with pytest.raises(ConfigError, match="floor is required"):
            FeatureStepConfig(kind=f"{HELPERS}:ValidatedTransform", params={})
        ok = FeatureStepConfig(
            kind=f"{HELPERS}:ValidatedTransform", params={"floor": 0.5}
        )
        assert is_class_ref(ok.kind)

    def test_unimportable_ref_defers_to_resolve(self, registry, synthetic_config):
        cfg = synthetic_config(  # constructs fine on any machine...
            features=FeatureConfig(steps=(FeatureStepConfig(kind="no.such.module:X"),))
        )
        with pytest.raises(ValueError, match="cannot import"):  # ...fails HERE
            resolve(cfg, asof="2026-01-01", registry=registry)

    def test_non_callable_target_refused_at_resolve(self, registry, synthetic_config):
        cfg = synthetic_config(
            features=FeatureConfig(
                steps=(FeatureStepConfig(kind=f"{HELPERS}:NOT_CALLABLE"),)
            )
        )
        with pytest.raises(ValueError, match="must import to a callable"):
            resolve(cfg, asof="2026-01-01", registry=registry)

    def test_apply_stream_steps_calls_the_target(self):
        from dskit.pipeline.records import MarketRecord
        from tests.pipeline.refhelpers import NOT_CALLABLE as _  # noqa: F401

        recs = [
            MarketRecord(
                venue="v",
                instrument="I",
                contract=f"C{i}",
                asof_ms=i,
                usable=True,
                reason="ok",
                mid=m,
            )
            for i, m in enumerate((0.2, 0.8))
        ]
        cfg = FeatureConfig(
            steps=(
                FeatureStepConfig(
                    kind=f"{HELPERS}:drop_low_mids", params={"floor": 0.5}
                ),
            )
        )
        assert [r.contract for r in apply_stream_steps(iter(recs), cfg)] == ["C1"]


class TestSinkRefs:
    def test_custom_sink_receives_the_run(self, registry, synthetic_config):
        from tests.pipeline.refhelpers import ListSink

        cfg = synthetic_config(
            tracking=TrackingConfig(sinks=(SinkConfig(kind=f"{HELPERS}:ListSink"),))
        )
        Runner(cfg, asof="2026-01-01", registry=registry).run()
        sink = ListSink.instances[-1]
        kinds = [k for k, _ in sink.events]
        assert kinds[0] == "params" and kinds[-1] == "close"
        assert kinds.count("metrics") == 5

    def test_seamless_shape_checked_at_resolve(self, registry, synthetic_config):
        cfg = synthetic_config(
            tracking=TrackingConfig(sinks=(SinkConfig(kind=f"{HELPERS}:NotASink"),))
        )
        with pytest.raises(ValueError, match="Tracker seam method"):
            resolve(cfg, asof="2026-01-01", registry=registry)


class TestModelFamilyRefs:
    def _model(self, tmp_path, **kw):
        base = dict(
            name=f"{HELPERS}:ConstantModelFamily",
            model_dir=str(tmp_path / "models"),
            params={"q": 0.9, "n_events": 60},
        )
        base.update(kw)
        return ModelConfig(**base)

    def test_train_and_load_paths(self, registry, synthetic_config, tmp_path):
        from tests.pipeline.refhelpers import ConstantModelFamily

        cfg = synthetic_config(model=self._model(tmp_path))
        result = Runner(cfg, asof="2026-01-01", registry=registry).run()
        assert ("train", {"q": 0.9, "n_events": 60}) in ConstantModelFamily.calls
        assert result.stages[0].verdict.startswith("trained model")

        loaded = synthetic_config(
            model=self._model(tmp_path, mode="load", artifact="const/v1")
        )
        Runner(loaded, asof="2026-01-02", registry=registry).run()
        assert ("load", "const/v1") in ConstantModelFamily.calls

    def test_family_missing_load_refused_at_resolve(
        self, registry, synthetic_config, tmp_path
    ):
        cfg = synthetic_config(
            model=ModelConfig(
                name=f"{HELPERS}:drop_low_mids",  # a callable, but no .load
                model_dir=str(tmp_path / "m"),
                mode="load",
                artifact="x",
            )
        )
        with pytest.raises(ValueError, match="must expose callable.*'load'"):
            resolve(cfg, asof="2026-01-01", registry=registry)

    def test_non_signal_result_fails_loud(self, registry, synthetic_config, tmp_path):
        cfg = synthetic_config(
            model=ModelConfig(
                name=f"{HELPERS}:BadModelFamily", model_dir=str(tmp_path / "m")
            )
        )
        with pytest.raises(ValueError, match="SignalProvider seam"):
            Runner(cfg, asof="2026-01-01", registry=registry).run()


class TestOptimizerAndCustomStages:
    def test_custom_sizer_replaces_backend_optimize(self, registry, synthetic_config):
        cfg = synthetic_config(
            optimization=OptimizationConfig(
                kind=f"{HELPERS}:tally_sizer",
                params={"cap": 7, "whom": {"$from": "stat_test.survivors"}},
            )
        )
        result = Runner(cfg, asof="2026-01-01", registry=registry).run()
        opt = next(s for s in result.stages if s.stage == "optimize")
        assert opt.payload["kind"] == "tally"
        assert opt.payload["cap"] == 7
        assert opt.payload["survivors"] == list(result.survivors)

    def test_named_custom_stage_runs_in_place(self, registry, synthetic_config):
        cfg = synthetic_config(
            stages=(
                "train",
                "validate",
                "stat_test",
                f"report={HELPERS}:report_stage",
                "optimize",
                "backtest",
            )
        )
        result = Runner(cfg, asof="2026-01-01", registry=registry).run()
        names = [s.stage for s in result.stages]
        assert names[3] == "report"
        report = result.stages[3]
        assert report.verdict.startswith("report:")
        assert report.payload["n_survivors"] == len(result.survivors)
        import os

        assert os.path.exists(os.path.join(result.run_dir, "stages", "04-report.json"))

    def test_custom_stage_must_return_a_dict(self, registry, synthetic_config):
        cfg = synthetic_config(
            stages=("train", "validate", "stat_test", f"bad={HELPERS}:broken_stage")
        )
        with pytest.raises(ValueError, match="payload dict"):
            Runner(cfg, asof="2026-01-01", registry=registry).run()

    def test_stage_list_rules_for_custom_entries(self, synthetic_config):
        with pytest.raises(ConfigError, match="shadows a canonical"):
            synthetic_config(
                stages=("train", "validate", f"train={HELPERS}:report_stage")
            )
        with pytest.raises(ConfigError, match="not a valid"):
            synthetic_config(stages=("train", "validate", "rep=not a ref"))
        with pytest.raises(ConfigError, match="must match"):
            synthetic_config(
                stages=("train", "validate", f"Bad Name={HELPERS}:report_stage")
            )

    def test_refs_may_target_custom_stage_outputs(self, registry, synthetic_config):
        cfg = synthetic_config(
            stages=(
                "train",
                "validate",
                "stat_test",
                f"report={HELPERS}:report_stage",
                "optimize",
                "backtest",
            ),
            optimization=OptimizationConfig(
                kind=f"{HELPERS}:tally_sizer",
                params={"cap": {"$from": "report.n_survivors"}},
            ),
        )
        result = Runner(cfg, asof="2026-01-01", registry=registry).run()
        opt = next(s for s in result.stages if s.stage == "optimize")
        assert opt.payload["cap"] == len(result.survivors)  # wired from report


#: The one sentence core says about an unimplemented hook. Both doorways
#: below must END with it, which is what makes "the pack imports the rule"
#: checkable: a second, differently-worded copy anywhere fails this.
ABSTRACT_TAIL = "is abstract (missing ['step'])"

THIS = "tests.pipeline.test_class_refs"


class TestLibraryClassDoorway:
    """``import_library_class`` — the shared door EVERY declared library
    class enters through (torch module, torch optimizer, torch adapter,
    transformer config/model)."""

    def test_a_class_with_unimplemented_hooks_is_refused_by_the_doorway(self):
        """Not usable here for the same reason ``requires=`` exists: refused
        BY NAME at the door rather than as a raw ``ABCMeta`` TypeError deep
        inside a training loop."""
        with pytest.raises(ValueError) as caught:
            import_library_class(f"{THIS}:HalfWrittenPlain", "torch adapter")
        message = str(caught.value)
        assert f"{THIS}:HalfWrittenPlain" in message
        assert message.endswith(ABSTRACT_TAIL)

    def test_a_complete_class_still_passes(self):
        cls = import_library_class("dskit.pipeline.synthetic_nodes:SynthClip", "x")
        assert cls.__name__ == "SynthClip"

    def test_both_core_doorways_word_abstractness_identically(self):
        """The pin behind "a tier-2 pack never restates tier-1 truth": the
        registration door (``node_class_errors``) and the import door say
        the same sentence about the same defect, because they ask the same
        core helper. Two copies drift; one cannot."""
        registration = [
            p for p in node_class_errors(HalfWrittenNode, "here") if "abstract" in p
        ]
        with pytest.raises(ValueError) as caught:
            import_library_class(f"{THIS}:HalfWrittenNode", "here")
        assert registration and registration[0].endswith(ABSTRACT_TAIL)
        assert str(caught.value).endswith(ABSTRACT_TAIL)
