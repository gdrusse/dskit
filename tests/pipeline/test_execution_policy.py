"""ADR-0091: ``dskit/pipeline/policy.py`` — classification before construction.

``classify_plan`` is the earliest boundary a serving run has: it walks a
planned DAG and asks a policy what each node's serving effect is, BEFORE
anything is instantiated. Two properties make that worth a module of its
own, and both are tested here:

* it constructs NOTHING — a data node's ``__init__`` scans its stream,
  so a classifier that instantiated first would have taken the mutable
  read it exists to gate;
* it accumulates. An out-of-vocabulary answer for three nodes is three
  problems in one ``ConfigError``, not the first one only.

Entry dominance and the "sole ``entry_read``" rule are production's
(``ServingExecutionPolicy``); this module knows only the closed
vocabulary and refuses anything outside it.
"""

import ast
import contextlib
import pathlib
import sys

import pytest

import dskit.pipeline
from dskit.pipeline.base import ConfigError
from dskit.pipeline.planner import plan
from tests.pipeline.dochelpers import banking_document, make_registry

MISSING = object()


def policy_module():
    """The new module, imported lazily so the file still collects without it."""
    import dskit.pipeline.policy as policy_module_

    return policy_module_


def execution_policy():
    """The ``ExecutionPolicy`` ABC."""
    return policy_module().ExecutionPolicy


def classify_plan():
    """The ``classify_plan`` walker."""
    return policy_module().classify_plan


def recording_policy(effects=None, default="pure"):
    """A policy that records every classify call and answers from a map."""

    class Recording(execution_policy()):
        def __init__(self):
            self.calls = []

        def classify(self, key, cls, params, evidence):
            self.calls.append((key, cls, params, evidence))
            return (effects or {}).get(key, default)

    return Recording()


@contextlib.contextmanager
def no_construction(classes):
    """Make every one of ``classes`` refuse to be instantiated."""

    def refuse(self, *args, **kwargs):
        raise AssertionError(f"{type(self).__name__} was constructed")

    saved = [(cls, vars(cls).get("__init__", MISSING)) for cls in classes]
    try:
        for cls in classes:
            cls.__init__ = refuse
        yield
    finally:
        for cls, original in saved:
            if original is MISSING:
                del cls.__init__
            else:
                cls.__init__ = original


@pytest.fixture
def the_plan():
    return plan(banking_document(), make_registry())


# ---------------------------------------------------------------------------
# The ABC
# ---------------------------------------------------------------------------


class TestExecutionPolicy:
    def test_classify_is_abstract_so_an_incomplete_policy_cannot_construct(self):
        class Incomplete(execution_policy()):
            pass

        with pytest.raises(TypeError):
            Incomplete()

    def test_a_policy_that_classifies_constructs(self):
        assert recording_policy().classify("k", object, {}, {}) == "pure"

    def test_defer_and_reader_are_concrete_and_default_to_no_op(self):
        policy = recording_policy()
        assert policy.defer("any-key") is False
        assert policy.reader("any-key") is None

    def test_the_module_exports_only_its_two_public_names(self):
        module = policy_module()
        assert "ExecutionPolicy" in module.__all__
        assert "classify_plan" in module.__all__
        assert [n for n in module.__all__ if n.startswith("_")] == []


# ---------------------------------------------------------------------------
# classify_plan
# ---------------------------------------------------------------------------


class TestClassifyPlan:
    def test_it_walks_the_plan_order_and_answers_every_key(self, the_plan):
        policy = recording_policy()
        result = classify_plan()(the_plan, policy, {})
        assert [call[0] for call in policy.calls] == list(the_plan.order)
        assert list(result) == list(the_plan.order)
        assert set(result.values()) == {"pure"}

    def test_it_passes_the_resolved_class_and_the_documents_params(self, the_plan):
        policy = recording_policy()
        classify_plan()(the_plan, policy, {})
        for key, cls, params, _evidence in policy.calls:
            assert cls is the_plan.resolved[key].cls
            assert params == the_plan.document.expanded[key].params

    def test_evidence_is_looked_up_per_key_and_defaults_to_empty(self, the_plan):
        first, second = the_plan.order[0], the_plan.order[1]
        evidence_by_key = {first: {"mode": "load", "artifact_pinned": True}}
        policy = recording_policy()
        classify_plan()(the_plan, policy, evidence_by_key)
        seen = {call[0]: call[3] for call in policy.calls}
        assert seen[first] == {"mode": "load", "artifact_pinned": True}
        assert seen[second] == {}
        assert all(evidence == {} for k, evidence in seen.items() if k != first)

    def test_an_answer_outside_the_vocabulary_refuses(self, the_plan):
        policy = recording_policy(default="read")
        with pytest.raises(ConfigError):
            classify_plan()(the_plan, policy, {})

    def test_every_offending_key_is_named_at_once(self, the_plan):
        bad = {key: "read" for key in the_plan.order[:3]}
        policy = recording_policy(effects=bad)
        with pytest.raises(ConfigError) as exc:
            classify_plan()(the_plan, policy, {})
        assert len(exc.value.errors) == 3
        text = str(exc.value)
        for key in bad:
            assert key in text
        assert "read" in text

    def test_it_constructs_no_node(self, the_plan):
        classes = {the_plan.resolved[key].cls for key in the_plan.order}
        policy = recording_policy()
        with no_construction(classes):
            result = classify_plan()(the_plan, policy, {})
        assert set(result) == set(the_plan.order)

    def test_a_refusal_still_constructs_no_node(self, the_plan):
        classes = {the_plan.resolved[key].cls for key in the_plan.order}
        policy = recording_policy(default="read")
        with no_construction(classes), pytest.raises(ConfigError):
            classify_plan()(the_plan, policy, {})

    def test_the_four_vocabulary_members_all_pass(self, the_plan):
        from dskit.pipeline.node import SERVING_EFFECTS

        for effect in SERVING_EFFECTS:
            policy = recording_policy(default=effect)
            result = classify_plan()(the_plan, policy, {})
            assert set(result.values()) == {effect}


# ---------------------------------------------------------------------------
# Purity: the module the STRUCTURAL planner calls may not reach production
# ---------------------------------------------------------------------------


def module_imports(path):
    """Every module name imported by ``path``, at any depth."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


class TestPurity:
    def path(self):
        return pathlib.Path(dskit.pipeline.__file__).parent / "policy.py"

    def test_the_module_exists_beside_the_planner(self):
        assert self.path().is_file()

    def test_it_imports_only_stdlib_and_the_pipeline(self):
        offenders = sorted(
            name
            for name in module_imports(self.path())
            if not (
                name.split(".")[0] in sys.stdlib_module_names
                or name == "dskit.pipeline"
                or name.startswith("dskit.pipeline.")
            )
        )
        assert offenders == []

    def test_it_never_names_dskit_production_at_any_depth(self):
        offenders = sorted(
            name
            for name in module_imports(self.path())
            if name == "dskit.production" or name.startswith("dskit.production.")
        )
        assert offenders == []
        assert "dskit.production" not in self.path().read_text(encoding="utf-8")
