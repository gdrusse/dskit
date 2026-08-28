"""The Node ABC, the kind registry, and ``uses`` resolution (docs/24 §1)."""

import pytest

from dskit.pipeline.base import ConfigError
from dskit.pipeline.node import (
    Node,
    NodeContext,
    NodeKindRegistry,
    TrainableNode,
    node_class_errors,
    resolve_uses,
)


class MinimalNode(Node):
    role = "transform"

    def run(self, ctx, inputs):
        return {"out": 1}


class ContractNode(Node):
    role = "signal"
    outputs = ("signal", "artifact")

    def run(self, ctx, inputs):
        return {"signal": {}, "artifact": ""}


class FussyNode(Node):
    role = "transform"

    @classmethod
    def validate_params(cls, params):
        return ["halflife_ms must be > 0"] if params.get("halflife_ms", 1) <= 0 else []

    def run(self, ctx, inputs):
        return {}


class AbstractNode(Node):
    role = "transform"


class NotANode:
    role = "transform"


def ctx(tmp_path):
    return NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path))


class TestNodeABC:
    def test_run_is_abstract(self):
        with pytest.raises(TypeError):
            AbstractNode("a")

    def test_minimal_subclass_constructs_with_namespaced_log(self):
        node = MinimalNode("vol_adjusted", {"halflife_ms": 60000})
        assert node.log.name == "dskit.pipeline.vol_adjusted"
        assert node.params == {"halflife_ms": 60000}
        assert (node.mode, node.artifact) == (None, "")

    def test_mode_and_artifact_are_stored(self):
        node = MinimalNode("m", mode="load", artifact="runs/x/model.json")
        assert (node.mode, node.artifact) == ("load", "runs/x/model.json")

    @pytest.mark.parametrize("bad", ["Bad-Key", "1x", ""])
    def test_bad_key_refused(self, bad):
        with pytest.raises(ConfigError, match="node key"):
            MinimalNode(bad)

    def test_non_dict_params_refused(self):
        with pytest.raises(ConfigError, match="params must be a dict"):
            MinimalNode("a", params=[1, 2])

    def test_validate_params_problems_block_construction(self):
        with pytest.raises(ConfigError, match="halflife_ms"):
            FussyNode("f", {"halflife_ms": -1})
        FussyNode("f", {"halflife_ms": 10})

    def test_params_are_deep_copied(self):
        raw = {"where": [{"field": "mid"}]}
        node = MinimalNode("a", raw)
        raw["where"][0]["field"] = "mutated"
        assert node.params["where"][0]["field"] == "mid"

    def test_default_validators_accept_anything(self):
        node = MinimalNode("a")
        assert MinimalNode.validate_params({"unknown": 1}) == []
        assert node.validate_inputs({"whatever": object()}) == []

    def test_default_fingerprint_is_none(self):
        assert MinimalNode("a").fingerprint() is None


class Trainable(TrainableNode):
    """The shape ADR-0038 ports every fit kind to: two hooks, no branch."""

    role = "train"

    def run_train(self, ctx, inputs):
        return {"out": "trained"}

    def run_load(self, ctx, inputs):
        return {"out": f"loaded:{self.artifact}"}


class PinnedInference(Trainable):
    """A pinned-inference kind: an UNSET mode means load, and train is the
    refusal."""

    role = "signal"
    default_mode = "load"

    def run_train(self, ctx, inputs):
        raise ValueError(f"{self.key}: mode='train' — this node never fits")


class HalfTrainable(TrainableNode):
    """Only one hook — abstract, so it must refuse at CONSTRUCTION."""

    role = "train"

    def run_train(self, ctx, inputs):
        return {}


class ValidatingTrainable(Trainable):
    """Every validation hook answers, so the additive order is visible."""

    def validate_common_inputs(self, inputs):
        return ["common"]

    def validate_train_inputs(self, inputs):
        return ["train"]

    def validate_load_inputs(self, inputs):
        return ["load"]


MISSING = "no artifact reference — pin one"


class TestTrainableNode:
    def test_run_is_the_template_method_dispatching_on_effective_mode(self, tmp_path):
        frame = ctx(tmp_path)
        assert Trainable("t").run(frame, {}) == {"out": "trained"}
        assert Trainable("t", mode="train").run(frame, {}) == {"out": "trained"}
        loader = Trainable("t", mode="load", artifact="a/model.pt")
        assert loader.run(frame, {}) == {"out": "loaded:a/model.pt"}

    def test_default_mode_decides_what_an_unset_mode_means(self, tmp_path):
        assert Trainable("t").effective_mode == "train"
        assert Trainable("t", mode="load", artifact="a").effective_mode == "load"
        assert PinnedInference("p").effective_mode == "load"
        assert PinnedInference("p", mode="train").effective_mode == "train"
        assert PinnedInference("p").run(ctx(tmp_path), {}) == {"out": "loaded:"}
        with pytest.raises(ValueError, match="never fits"):
            PinnedInference("p", mode="train").run(ctx(tmp_path), {})

    def test_both_hooks_are_abstract_so_a_half_class_cannot_construct(self):
        with pytest.raises(TypeError):
            HalfTrainable("h")
        assert "abstract" in "; ".join(node_class_errors(TrainableNode, "base"))

    def test_both_template_methods_resolve_to_the_base(self):
        for cls in (Trainable, PinnedInference, ValidatingTrainable):
            assert cls.run is TrainableNode.run
            assert cls.validate_inputs is TrainableNode.validate_inputs

    def test_validate_inputs_dispatches_additively_by_effective_mode(self):
        assert Trainable("t").validate_inputs({}) == []
        assert ValidatingTrainable("v").validate_inputs({}) == ["common", "train"]
        loader = ValidatingTrainable("v", mode="load", artifact="a")
        assert loader.validate_inputs({}) == ["common", "load"]


class TestNodeArtifactServices:
    def test_a_plain_node_declares_no_node_level_pin(self):
        assert MinimalNode("m").node_level_pin() is None
        # Even directly constructed with one: the hook, not a type test,
        # is what makes a pin visible (ADR-0038).
        assert MinimalNode("m", mode="load", artifact="a").node_level_pin() is None

    def test_a_trainable_pin_exists_exactly_when_the_document_wrote_load(self):
        assert Trainable("t").node_level_pin() is None
        assert Trainable("t", mode="train").node_level_pin() is None
        assert Trainable("t", mode="load", artifact="a").node_level_pin() == "a"
        assert Trainable("t", mode="load", artifact="").node_level_pin() == ""

    def test_the_node_level_pin_wins_and_a_restatement_is_allowed(self):
        node = Trainable("t", mode="load", artifact="a")
        assert node.pinned_artifact(missing=MISSING) == "a"
        assert node.pinned_artifact("a", missing=MISSING) == "a"
        assert node.pinned_artifact(None, "wired", missing=MISSING) == "a"

    def test_a_contradicting_declared_param_refuses(self):
        node = Trainable("t", mode="load", artifact="a")
        with pytest.raises(ValueError, match="disagree"):
            node.pinned_artifact("b", missing=MISSING)
        with pytest.raises(ValueError, match="one source of truth"):
            node.pinned_artifact("b", missing=MISSING)

    def test_an_empty_node_level_pin_refuses_by_name(self):
        node = Trainable("t", mode="load", artifact="")
        with pytest.raises(ValueError, match="empty artifact reference"):
            node.pinned_artifact("b", "w", missing=MISSING)

    def test_a_falsy_declared_or_wired_value_counts_as_absent(self):
        node = Trainable("t")
        assert node.pinned_artifact("d", "w", missing=MISSING) == "d"
        assert node.pinned_artifact(None, "w", missing=MISSING) == "w"
        assert node.pinned_artifact("", "w", missing=MISSING) == "w"
        with pytest.raises(ValueError, match="no artifact reference"):
            node.pinned_artifact(None, None, missing=MISSING)
        with pytest.raises(ValueError, match="no artifact reference"):
            node.pinned_artifact("", "", missing=MISSING)

    def test_the_missing_refusal_names_the_node(self):
        with pytest.raises(ValueError, match=r"^t: no artifact reference"):
            Trainable("t").pinned_artifact(missing=MISSING)

    def test_pin_port_problems_accepts_absent_and_refuses_an_empty_wire(self):
        node = MinimalNode("m")
        hint = "wire it from a train node"
        assert node.pin_port_problems({}, "artifact_path", hint=hint) == []
        assert node.pin_port_problems(None, "artifact_path", hint=hint) == []
        assert (
            node.pin_port_problems({"artifact_path": None}, "artifact_path", hint=hint)
            == []
        )
        assert (
            node.pin_port_problems({"artifact_path": "p"}, "artifact_path", hint=hint)
            == []
        )
        assert node.pin_port_problems(
            {"artifact_path": ""}, "artifact_path", hint=hint
        ) == [
            "artifact_path must be a non-empty string (wire it from a train "
            "node), got ''"
        ]
        assert node.pin_port_problems({"artifact_path": 7}, "artifact_path", hint=hint)


class TestValidateOutputs:
    def test_non_dict_and_bad_keys_refused(self):
        node = MinimalNode("a")
        assert node.validate_outputs(["not", "a", "dict"])
        assert node.validate_outputs({1: "x"})
        assert node.validate_outputs({"": "x"})

    def test_declared_contract_must_match_exactly(self):
        node = ContractNode("c")
        assert node.validate_outputs({"signal": {}, "artifact": ""}) == []
        missing = node.validate_outputs({"signal": {}})
        assert missing and "missing ['artifact']" in missing[0]
        extra = node.validate_outputs({"signal": {}, "artifact": "", "debug": 1})
        assert extra and "undeclared ['debug']" in extra[0]

    def test_undeclared_contract_accepts_any_named_outputs(self):
        assert MinimalNode("a").validate_outputs({"anything": 1}) == []


class TestArtifacts:
    def test_write_artifact_lands_under_the_node_dir(self, tmp_path):
        node = MinimalNode("qhat")
        path = node.write_artifact(ctx(tmp_path), "model.json", {"learn": 0.8})
        assert path.endswith("artifacts/qhat/model.json")
        import json

        with open(path, encoding="utf-8") as fh:
            assert json.load(fh) == {"learn": 0.8}

    def test_write_artifact_refuses_nan(self, tmp_path):
        with pytest.raises(ValueError):
            MinimalNode("a").write_artifact(ctx(tmp_path), "bad.json", float("nan"))


class TestNodeClassErrors:
    def test_good_class_has_no_problems(self):
        assert node_class_errors(MinimalNode, "here") == []

    def test_not_a_class_and_not_a_node(self):
        assert node_class_errors(lambda: None, "here")
        problems = node_class_errors(NotANode, "here")
        assert problems and "not a Node subclass" in problems[0]

    def test_abstract_class_refused(self):
        problems = node_class_errors(AbstractNode, "here")
        assert problems and "abstract" in problems[0]

    def test_missing_or_bad_role_refused(self):
        class NoRole(Node):
            def run(self, ctx, inputs):
                return {}

        class BadRole(Node):
            role = "banker"

            def run(self, ctx, inputs):
                return {}

        assert any("role" in p for p in node_class_errors(NoRole, "x"))
        assert any("role" in p for p in node_class_errors(BadRole, "x"))

    def test_bad_outputs_declaration_refused(self):
        class BadOutputs(Node):
            role = "transform"
            outputs = ["list", "not", "tuple"]

            def run(self, ctx, inputs):
                return {}

        assert any("outputs" in p for p in node_class_errors(BadOutputs, "x"))


class TestRegistryAndResolveUses:
    def test_register_get_and_owned_flag(self):
        reg = NodeKindRegistry()
        reg.register("minimal", MinimalNode)
        reg.register("stat_test", ContractNode, owned=True)
        assert reg.get("minimal") == (MinimalNode, False)
        assert reg.get("stat_test") == (ContractNode, True)
        assert "minimal" in reg and reg.kinds() == ("minimal", "stat_test")

    def test_duplicate_and_invalid_registrations_refused(self):
        reg = NodeKindRegistry()
        reg.register("minimal", MinimalNode)
        with pytest.raises(ValueError, match="already registered"):
            reg.register("minimal", ContractNode)
        with pytest.raises(ValueError, match="kind name"):
            reg.register("Not-Valid", MinimalNode)
        with pytest.raises(ValueError, match="not a Node subclass"):
            reg.register("bad", NotANode)

    def test_unknown_kind_names_the_registered_set(self):
        reg = NodeKindRegistry()
        reg.register("minimal", MinimalNode)
        with pytest.raises(ValueError, match=r"registered: \['minimal'\]"):
            reg.get("ghost")

    def test_resolve_uses_registered_name_and_import_path(self):
        reg = NodeKindRegistry()
        reg.register("minimal", MinimalNode)
        assert resolve_uses("minimal", reg).cls is MinimalNode
        resolved = resolve_uses("dskit.pipeline.synthetic_nodes:SynthClip", reg)
        assert resolved.cls.__name__ == "SynthClip" and not resolved.owned

    def test_resolve_uses_refuses_non_node_and_abstract_imports(self):
        reg = NodeKindRegistry()
        with pytest.raises(ValueError, match="not a Node subclass"):
            resolve_uses("tests.pipeline.test_node:NotANode", reg)
        with pytest.raises(ValueError, match="abstract"):
            resolve_uses("tests.pipeline.test_node:AbstractNode", reg)
        with pytest.raises(ValueError, match="cannot import"):
            resolve_uses("no.such.module:Thing", reg)
