"""The Node ABC, the kind registry, and ``uses`` resolution (docs/24 §1)."""

import pytest

from dskit.pipeline.base import ConfigError
from dskit.pipeline.node import (
    Node,
    NodeContext,
    NodeKindRegistry,
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
