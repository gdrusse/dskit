"""ADR-0091: ``SubgraphRunner`` — the search seam's engine, made public.

The extraction has to preserve four things that are contract rather than
incident, and each has its own section below:

* ``needed`` is a CONSTRUCTOR argument — a runner given ``needed = {a}``
  never runs a dirty descendant ``b`` outside that ancestry;
* ``outputs`` is passed in, mutated in place and handed back as the SAME
  object, so a trial can work on a scratch copy while the winner pass
  writes the live dict;
* ``prev_bindings`` is an OUT parameter — the ``$prev`` resolutions of
  this pass land in the dict the caller supplied;
* the override rule the runner keeps is exactly two clauses (a DECLARED
  node, an EXISTING param path) and the declared-node clause fires
  first. The unsearchable-role refusal is a SEARCH rule and stays with
  ``_SearchSeam`` — serving's one override addresses a ``data`` node's
  window, and a verbatim extraction would have refused it.

Plus the two things the seam is being extracted FOR: a policy may defer
a key (the entry is executed once, elsewhere, and its output is seeded)
and may hand a node a ``ReleaseReader`` through ``ctx.release_reader``.

The search suites are the regression guard: ``test_kinds_search.py``
must keep passing untouched, so its fixtures are IMPORTED here rather
than restated, and the last section re-pins the parabola's numbers.
"""

import ast
import inspect
import pathlib

import pytest

from dskit.pipeline import driver
from dskit.pipeline.base import ConfigError
from dskit.pipeline.document import NodeSpec, PipelineDocument
from dskit.pipeline.driver import _materialize, _SearchSeam, run_document
from dskit.pipeline.node import Node, NodeContext
from dskit.pipeline.planner import plan
from tests.pipeline.test_kinds_search import (
    ASOF,
    FLAT_SPLITS,
    parabola_document,
    parabola_pipeline,
    search_registry,
)

HERE = "tests.pipeline.test_subgraph_runner:"


# ---------------------------------------------------------------------------
# A four-node fixture graph: src -> mid -> {a, b}, plus a gate off mid.
# `a` is the objective-shaped head, `b` the dirty descendant OUTSIDE it.
# ---------------------------------------------------------------------------


class Src(Node):
    """A literal source (role ``data``) — the shape serving overrides."""

    role = "data"
    outputs = ("x",)

    def run(self, ctx, inputs):
        return {"x": self.params.get("x", 1)}


class Relay(Node):
    """A pointwise relay whose ``bump`` is the override head."""

    role = "transform"
    outputs = ("x",)

    def run(self, ctx, inputs):
        return {"x": inputs["x"] + self.params.get("bump", 0)}


class Head(Node):
    """A terminal arithmetic head; ``bias`` carries the ``$prev`` binding."""

    role = "transform"
    outputs = ("y",)

    def run(self, ctx, inputs):
        return {
            "y": inputs["x"] * self.params.get("mul", 1) + self.params.get("bias", 0.0)
        }


class Gate(Node):
    """GO iff ``n >= bar`` (role ``gate``) — the verdict-flip subject."""

    role = "gate"
    outputs = ("n", "verdict")

    def run(self, ctx, inputs):
        n = inputs["n"]
        return {"n": n, "verdict": "GO" if n >= self.params.get("bar", 0) else "NO-GO"}


class Picky(Node):
    """Refuses a poisoned upstream value — the ``validate_inputs`` subject."""

    role = "transform"
    outputs = ("x",)

    def validate_inputs(self, inputs):
        return ["x must not be 'poison'"] if inputs.get("x") == "poison" else []

    def run(self, ctx, inputs):
        return {"x": inputs["x"]}


class BadContract(Node):
    """Declares ``x`` and returns ``y`` — the output-contract subject."""

    role = "transform"
    outputs = ("x",)

    def run(self, ctx, inputs):
        return {"y": 1}


class ReaderProbe(Node):
    """Records the ``ctx.release_reader`` each execution was handed."""

    role = "transform"
    outputs = ("seen",)
    seen = []

    def run(self, ctx, inputs):
        ReaderProbe.seen.append(ctx.release_reader)
        return {"seen": len(ReaderProbe.seen)}


def fixture_pipeline():
    """src -> mid -> {a, b}; guard hangs off mid. Fresh specs each call."""
    return {
        "src": NodeSpec(uses=HERE + "Src", params={"x": 1}),
        "mid": NodeSpec(uses=HERE + "Relay", inputs={"x": "$src.x"}, params={"bump": 0}),
        "a": NodeSpec(
            uses=HERE + "Head",
            inputs={"x": "$mid.x"},
            params={"mul": 1, "bias": {"$prev": "a.y", "default": 0.0}},
        ),
        "b": NodeSpec(uses=HERE + "Head", inputs={"x": "$mid.x"}, params={"mul": 10}),
        "guard": NodeSpec(
            uses=HERE + "Gate", inputs={"n": "$mid.x"}, params={"bar": 0}
        ),
    }


def reader_pipeline():
    """src -> probe: the smallest graph a release reader can reach."""
    return {
        "src": NodeSpec(uses=HERE + "Src", params={"x": 1}),
        "probe": NodeSpec(uses=HERE + "ReaderProbe", inputs={"x": "$src.x"}),
    }


def planned(pipeline=None):
    """Plan one fixture document — no registry, every ``uses`` an import path."""
    document = PipelineDocument(name="runner", pipeline=pipeline or fixture_pipeline())
    return plan(document)


def a_ctx(tmp_path, **over):
    return NodeContext(name="runner", asof=ASOF, run_dir=str(tmp_path), **over)


def base_pass(the_plan, ctx, prev=None):
    """Execute every node once — the base outputs a runner reads from.

    Built with the driver's own :func:`_materialize` so the seeded
    outputs are exactly what an ordinary run would have produced.
    """
    prev = prev or {}
    outputs = {}
    for key in the_plan.order:
        spec = the_plan.document.expanded[key]
        params = _materialize(
            spec.params, f"pipeline.{key}.params", outputs, {}, prev, {}
        )
        inputs = {
            port: _materialize(
                ref, f"pipeline.{key}.inputs.{port}", outputs, {}, prev, {}
            )
            for port, ref in spec.inputs.items()
        }
        node = the_plan.resolved[key].cls(
            key, params, mode=spec.mode, artifact=spec.artifact
        )
        outputs[key] = node.run(ctx, inputs)
    return outputs


def a_runner(the_plan, needed, outputs, policy=None, prev=None):
    """Construct the seam under test, positionally, as §9.1 spells it."""
    return driver.SubgraphRunner(the_plan, needed, outputs, {}, prev or {}, policy)


def a_policy(defers=(), readers=None):
    """A real :class:`ExecutionPolicy` that defers/serves the named keys."""
    from dskit.pipeline.policy import ExecutionPolicy

    class Fake(ExecutionPolicy):
        def classify(self, key, cls, params, evidence):
            return "pure"

        def defer(self, key):
            return key in defers

        def reader(self, key):
            return (readers or {}).get(key)

    return Fake()


@pytest.fixture
def graph(tmp_path):
    """``(plan, ctx, needed, base_outputs)`` for the fixture graph."""
    the_plan = planned()
    ctx = a_ctx(tmp_path)
    needed = the_plan.ancestors("a") | {"a"}
    return the_plan, ctx, needed, base_pass(the_plan, ctx)


# ---------------------------------------------------------------------------
# The three details §9.1 calls contract
# ---------------------------------------------------------------------------


class TestNeeded:
    def test_the_fixture_graph_is_the_shape_the_tests_assume(self, graph):
        the_plan, _ctx, needed, base = graph
        assert the_plan.order == ("src", "mid", "a", "b", "guard")
        assert needed == {"src", "mid", "a"}
        assert the_plan.descendants("mid") == {"a", "b", "guard"}
        assert base["a"] == {"y": 1.0} and base["b"] == {"y": 10}

    def test_a_dirty_descendant_outside_needed_never_runs(self, graph):
        the_plan, ctx, needed, base = graph
        runner = a_runner(the_plan, needed, base)
        outputs, ran, seconds = runner.rerun({"mid.bump": 5}, base, ctx, {})
        assert ran == ("mid", "a")
        assert set(seconds) == {"mid", "a"}
        assert outputs["mid"] == {"x": 6}
        assert outputs["a"] == {"y": 6.0}
        # b and guard are dirty but outside the ancestry the caller asked for.
        assert outputs["b"] == {"y": 10}
        assert outputs["guard"] == {"n": 1, "verdict": "GO"}

    def test_widening_needed_widens_the_subgraph(self, graph):
        the_plan, ctx, _needed, base = graph
        runner = a_runner(the_plan, set(the_plan.order), base)
        _outputs, ran, _seconds = runner.rerun({"mid.bump": 5}, base, ctx, {})
        assert ran == ("mid", "a", "b", "guard")


class TestOutputsInPlace:
    def test_the_same_dict_is_mutated_and_returned(self, graph):
        the_plan, ctx, needed, base = graph
        runner = a_runner(the_plan, needed, base)
        outputs, _ran, _seconds = runner.rerun({"mid.bump": 5}, base, ctx, {})
        assert outputs is base

    def test_a_scratch_copy_leaves_the_base_outputs_untouched(self, graph):
        the_plan, ctx, needed, base = graph
        runner = a_runner(the_plan, needed, base)
        scratch = dict(base)
        outputs, _ran, _seconds = runner.rerun({"mid.bump": 5}, scratch, ctx, {})
        assert outputs is scratch
        assert scratch["a"] == {"y": 6.0}
        assert base["a"] == {"y": 1.0}
        assert base["mid"] == {"x": 1}

    def test_the_documents_params_are_never_mutated(self, graph):
        the_plan, ctx, needed, base = graph
        runner = a_runner(the_plan, needed, base)
        runner.rerun({"mid.bump": 5}, dict(base), ctx, {})
        assert the_plan.document.expanded["mid"].params == {"bump": 0}


class TestPrevBindingsIsAnOutParameter:
    def test_a_default_binding_lands_in_the_callers_dict(self, graph):
        the_plan, ctx, needed, base = graph
        runner = a_runner(the_plan, needed, base)
        bindings = {}
        runner.rerun({"mid.bump": 5}, dict(base), ctx, bindings)
        assert bindings == {"a.y": "default"}

    def test_a_resolved_prev_binding_lands_there_too_and_moves_the_value(
        self, tmp_path
    ):
        the_plan = planned()
        ctx = a_ctx(tmp_path)
        prev = {"a": {"y": 4.0}}
        base = base_pass(the_plan, ctx, prev=prev)
        assert base["a"] == {"y": 5.0}
        needed = the_plan.ancestors("a") | {"a"}
        runner = a_runner(the_plan, needed, base, prev=prev)
        bindings = {}
        outputs, _ran, _seconds = runner.rerun(
            {"mid.bump": 5}, dict(base), ctx, bindings
        )
        assert bindings == {"a.y": "prev"}
        assert outputs["a"] == {"y": 10.0}


# ---------------------------------------------------------------------------
# The override rule the runner keeps — and the one it does NOT
# ---------------------------------------------------------------------------


class TestOverrideRule:
    def test_an_undeclared_node_refuses_with_todays_exact_words(self, graph):
        the_plan, ctx, needed, base = graph
        runner = a_runner(the_plan, needed, base)
        with pytest.raises(ValueError) as exc:
            runner.rerun({"ghost.x": 1}, dict(base), ctx, {})
        assert str(exc.value) == (
            "override 'ghost.x' must be '<node>.<param.path>' addressing a "
            f"declared node (declared: {sorted(the_plan.document.expanded)})"
        )

    @pytest.mark.parametrize("target", ["mid", "", "Mid.bump"])
    def test_a_target_that_is_not_node_dot_param_refuses(self, graph, target):
        the_plan, ctx, needed, base = graph
        runner = a_runner(the_plan, needed, base)
        with pytest.raises(ValueError) as exc:
            runner.rerun({target: 1}, dict(base), ctx, {})
        assert "must be '<node>.<param.path>'" in str(exc.value)

    def test_a_non_dict_override_map_refuses(self, graph):
        the_plan, ctx, needed, base = graph
        runner = a_runner(the_plan, needed, base)
        with pytest.raises(ValueError) as exc:
            runner.rerun("boom", dict(base), ctx, {})
        assert "overrides must be a dict" in str(exc.value)

    def test_the_declared_node_check_fires_before_anything_indexes_the_plan(
        self, graph
    ):
        # Plan.role_of indexes resolved[key]: an unsearchability test placed
        # first would raise KeyError where today a ValueError names the typo.
        the_plan, ctx, needed, base = graph
        runner = a_runner(the_plan, needed, base)
        with pytest.raises(Exception) as exc:
            runner.rerun({"ghost.x": 1}, dict(base), ctx, {})
        assert type(exc.value) is ValueError
        assert "ghost.x" in str(exc.value)

    def test_a_missing_param_path_refuses_with_apply_param_overrides_words(
        self, graph
    ):
        the_plan, ctx, needed, base = graph
        runner = a_runner(the_plan, needed, base)
        scratch = dict(base)
        with pytest.raises(ValueError) as exc:
            runner.rerun({"mid.ghost": 1}, scratch, ctx, {})
        assert str(exc.value) == (
            "override 'mid.ghost': 'ghost' is not an existing param "
            "(available: ['bump']) — overrides may only address existing "
            "params, never create them"
        )

    def test_a_missing_param_path_creates_nothing(self, graph):
        the_plan, ctx, needed, base = graph
        runner = a_runner(the_plan, needed, base)
        scratch = dict(base)
        before = {k: dict(v) for k, v in scratch.items()}
        with pytest.raises(ValueError):
            runner.rerun({"mid.ghost": 1}, scratch, ctx, {})
        assert scratch == before
        assert "ghost" not in the_plan.document.expanded["mid"].params

    def test_an_empty_override_map_executes_nothing(self, graph):
        the_plan, ctx, needed, base = graph
        runner = a_runner(the_plan, needed, base)
        outputs, ran, seconds = runner.rerun({}, base, ctx, {})
        assert outputs is base
        assert ran == ()
        assert seconds == {}

    def test_an_override_may_address_a_data_role_node(self, graph):
        # The whole reason the unsearchable-role check stays with the SEARCH
        # seam: serving's one override is a data node's window param.
        the_plan, ctx, needed, base = graph
        assert the_plan.role_of("src") == "data"
        runner = a_runner(the_plan, needed, base)
        outputs, ran, _seconds = runner.rerun({"src.x": 5}, base, ctx, {})
        assert ran == ("src", "mid", "a")
        assert outputs["src"] == {"x": 5}
        assert outputs["a"] == {"y": 5.0}


# ---------------------------------------------------------------------------
# The policy: deferral and the release reader
# ---------------------------------------------------------------------------


class TestDeferral:
    def test_a_deferred_key_is_skipped_and_its_seeded_output_feeds_descendants(
        self, graph
    ):
        the_plan, ctx, needed, base = graph
        runner = a_runner(the_plan, needed, base, policy=a_policy(defers={"src"}))
        seeded = dict(base)
        seeded["src"] = {"x": 99}
        outputs, ran, seconds = runner.rerun({"src.x": 5}, seeded, ctx, {})
        # The override still put src (and its descendants) into `dirty` —
        # that is what makes the subgraph non-empty — but src never ran, so
        # no second mutable read happened and 99 is what descendants saw.
        assert ran == ("mid", "a")
        assert set(seconds) == {"mid", "a"}
        assert outputs["src"] == {"x": 99}
        assert outputs["mid"] == {"x": 99}
        assert outputs["a"] == {"y": 99.0}

    def test_a_deferred_key_with_no_seeded_output_refuses(self, graph):
        the_plan, ctx, needed, base = graph
        runner = a_runner(the_plan, needed, base, policy=a_policy(defers={"src"}))
        unseeded = {k: v for k, v in base.items() if k != "src"}
        with pytest.raises(ValueError) as exc:
            runner.rerun({"src.x": 5}, unseeded, ctx, {})
        assert "src" in str(exc.value)

    def test_the_default_policy_defers_nothing(self, graph):
        the_plan, ctx, needed, base = graph
        runner = a_runner(the_plan, needed, base, policy=a_policy())
        outputs, ran, _seconds = runner.rerun({"src.x": 5}, dict(base), ctx, {})
        assert ran == ("src", "mid", "a")
        assert outputs["src"] == {"x": 5}


class TestReleaseReader:
    def test_the_policys_reader_reaches_the_node_as_ctx_release_reader(
        self, tmp_path
    ):
        the_plan = planned(reader_pipeline())
        ctx = a_ctx(tmp_path)
        ReaderProbe.seen.clear()
        base = base_pass(the_plan, ctx)
        assert ReaderProbe.seen == [None]
        ReaderProbe.seen.clear()
        sentinel = object()
        runner = a_runner(
            the_plan,
            set(the_plan.order),
            base,
            policy=a_policy(readers={"probe": sentinel}),
        )
        runner.rerun({"src.x": 2}, dict(base), ctx, {})
        assert ReaderProbe.seen == [sentinel]
        # The run's own frame is untouched — the reader is per node.
        assert ctx.release_reader is None

    def test_a_node_the_policy_gives_no_reader_sees_none(self, tmp_path):
        the_plan = planned(reader_pipeline())
        ctx = a_ctx(tmp_path)
        ReaderProbe.seen.clear()
        base = base_pass(the_plan, ctx)
        ReaderProbe.seen.clear()
        runner = a_runner(the_plan, set(the_plan.order), base, policy=a_policy())
        runner.rerun({"src.x": 2}, dict(base), ctx, {})
        assert ReaderProbe.seen == [None]

    def test_with_no_policy_at_all_a_node_still_sees_none(self, tmp_path):
        the_plan = planned(reader_pipeline())
        ctx = a_ctx(tmp_path)
        ReaderProbe.seen.clear()
        base = base_pass(the_plan, ctx)
        ReaderProbe.seen.clear()
        runner = a_runner(the_plan, set(the_plan.order), base)
        runner.rerun({"src.x": 2}, dict(base), ctx, {})
        assert ReaderProbe.seen == [None]


# ---------------------------------------------------------------------------
# guard_verdicts — the stale-GO refusal, unchanged
# ---------------------------------------------------------------------------


class TestGuardVerdicts:
    def flip(self, tmp_path):
        the_plan = planned()
        ctx = a_ctx(tmp_path)
        base = base_pass(the_plan, ctx)
        assert base["guard"]["verdict"] == "GO"
        needed = the_plan.ancestors("guard") | {"guard"}
        return the_plan, ctx, needed, base

    def test_a_gate_flipping_to_no_go_refuses(self, tmp_path):
        the_plan, ctx, needed, base = self.flip(tmp_path)
        runner = a_runner(the_plan, needed, base)
        with pytest.raises(ValueError) as exc:
            runner.rerun({"mid.bump": -5}, base, ctx, {}, guard_verdicts=True)
        assert str(exc.value) == (
            "guard: the winner pass flipped this gate node's verdict to NO-GO "
            "after halt decisions were made on the base pass — refusing to "
            "ride a stale GO"
        )

    def test_without_the_guard_the_flip_is_allowed(self, tmp_path):
        the_plan, ctx, needed, base = self.flip(tmp_path)
        runner = a_runner(the_plan, needed, base)
        outputs, ran, _seconds = runner.rerun({"mid.bump": -5}, base, ctx, {})
        assert ran == ("mid", "guard")
        assert outputs["guard"]["verdict"] == "NO-GO"

    def test_a_verdict_that_was_already_no_go_is_not_a_flip(self, tmp_path):
        the_plan, ctx, needed, base = self.flip(tmp_path)
        base["guard"] = {"n": -4, "verdict": "NO-GO"}
        runner = a_runner(the_plan, needed, base)
        outputs, _ran, _seconds = runner.rerun(
            {"mid.bump": -5}, base, ctx, {}, guard_verdicts=True
        )
        assert outputs["guard"]["verdict"] == "NO-GO"


# ---------------------------------------------------------------------------
# run_keys — the serving BASE PASS verb
# ---------------------------------------------------------------------------


class TestRunKeys:
    def test_it_runs_the_given_keys_in_plan_order(self, graph):
        the_plan, ctx, needed, base = graph
        runner = a_runner(the_plan, needed, base)
        outputs = {}
        got, ran, seconds = runner.run_keys(["a", "mid", "src"], outputs, ctx, {})
        assert got is outputs
        assert ran == ("src", "mid", "a")
        assert set(seconds) == {"src", "mid", "a"}
        assert outputs["a"] == {"y": 1.0}

    def test_it_applies_no_overrides(self, graph):
        the_plan, ctx, needed, base = graph
        runner = a_runner(the_plan, needed, base)
        outputs = {}
        runner.run_keys(the_plan.order, outputs, ctx, {})
        assert outputs == base

    def test_it_runs_only_the_keys_it_was_given(self, graph):
        the_plan, ctx, needed, base = graph
        runner = a_runner(the_plan, needed, base)
        outputs = {}
        _got, ran, _seconds = runner.run_keys(["src", "mid"], outputs, ctx, {})
        assert ran == ("src", "mid")
        assert set(outputs) == {"src", "mid"}

    def test_it_records_prev_bindings_into_the_callers_dict(self, graph):
        the_plan, ctx, needed, base = graph
        runner = a_runner(the_plan, needed, base)
        bindings = {}
        runner.run_keys(["src", "mid", "a"], {}, ctx, bindings)
        assert bindings == {"a.y": "default"}

    def test_an_undeclared_key_refuses(self, graph):
        the_plan, ctx, needed, base = graph
        runner = a_runner(the_plan, needed, base)
        with pytest.raises(ValueError) as exc:
            runner.run_keys(["ghost"], {}, ctx, {})
        assert "ghost" in str(exc.value)

    def test_an_empty_key_list_runs_nothing(self, graph):
        the_plan, ctx, needed, base = graph
        runner = a_runner(the_plan, needed, base)
        outputs = {}
        got, ran, seconds = runner.run_keys([], outputs, ctx, {})
        assert got is outputs
        assert ran == ()
        assert seconds == {}

    def test_a_deferred_key_is_skipped_here_too(self, graph):
        the_plan, ctx, needed, base = graph
        runner = a_runner(the_plan, needed, base, policy=a_policy(defers={"src"}))
        outputs = {"src": {"x": 7}}
        _got, ran, _seconds = runner.run_keys(["src", "mid", "a"], outputs, ctx, {})
        assert ran == ("mid", "a")
        assert outputs["src"] == {"x": 7}
        assert outputs["a"] == {"y": 7.0}


# ---------------------------------------------------------------------------
# One owner for apply_param_override, and the public surface
# ---------------------------------------------------------------------------


def driver_source():
    return pathlib.Path(inspect.getsourcefile(driver)).read_text(encoding="utf-8")


class TestPublicSurface:
    def test_the_runner_and_the_override_rule_are_exported(self):
        assert "SubgraphRunner" in driver.__all__
        assert "apply_param_override" in driver.__all__

    def test_apply_param_override_is_callable_and_public(self):
        params = {"opt": {"lr": 0.1}, "n": 1}
        driver.apply_param_override(params, "t", ("opt", "lr"), 0.9)
        driver.apply_param_override(params, "t", ("n",), 2)
        assert params == {"opt": {"lr": 0.9}, "n": 2}

    def test_the_driver_defines_no_second_copy_of_the_rule(self):
        assert "def _apply_param_override" not in driver_source()
        assert "def apply_param_override" in driver_source()

    def test_exactly_one_function_owns_the_rule(self):
        tree = ast.parse(driver_source())
        owners = sorted(
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.endswith("apply_param_override")
        )
        assert owners == ["apply_param_override"]

    def test_the_legacy_private_name_is_the_same_object(self):
        # tests/pipeline/test_kinds_search.py imports the private spelling
        # and must keep passing UNTOUCHED, so the name stays — as an alias,
        # never a second definition.
        assert driver._apply_param_override is driver.apply_param_override


# ---------------------------------------------------------------------------
# _SearchSeam keeps its own rule, its own attributes, and its behaviour
# ---------------------------------------------------------------------------


def search_seam(tmp_path, pipeline=None):
    """A real search seam over the parabola fixture, plus its base outputs."""
    document = parabola_document(tmp_path, pipeline=pipeline)
    the_plan = plan(document, search_registry())
    ctx = NodeContext(
        name="parabola", asof=ASOF, run_dir=str(tmp_path), splits=FLAT_SPLITS
    )
    base = {
        "src": {"x": 1},
        "theta": {"value": 10.0},
        "val": {"metrics": {"loss": 49.0, "n": 1}},
    }
    seam = _SearchSeam("search", the_plan, base, {}, {}, trial_ctx=ctx)
    return seam, ctx, base


class TestSearchSeamUnchanged:
    def test_it_still_exposes_needed_seed_targets_and_calls(self, tmp_path):
        pipeline = parabola_pipeline(
            theta_params={"theta": 10.0, "seed": 0},
            search_params={"space": {"theta.theta": [0.0, 3.0]}, "seeds": [0, 2]},
        )
        seam, _ctx, _base = search_seam(tmp_path, pipeline)
        assert seam.needed == {"src", "theta", "val"}
        assert seam.seed_targets == ("theta.seed",)
        assert seam.calls == 0
        assert callable(seam.apply_winner)

    def test_a_trial_scores_the_objective_and_counts_the_call(self, tmp_path):
        seam, _ctx, base = search_seam(tmp_path)
        assert seam({"theta.theta": 3.0}) == 0.0
        assert seam.calls == 1
        assert base["theta"] == {"value": 10.0}

    def test_apply_winner_replaces_the_live_outputs(self, tmp_path):
        seam, ctx, base = search_seam(tmp_path)
        reran, seconds = seam.apply_winner({"theta.theta": 3.0}, ctx, {})
        assert reran == ("theta", "val")
        assert set(seconds) == {"theta", "val"}
        assert base["theta"] == {"value": 3.0}
        assert base["val"]["metrics"]["loss"] == 0.0

    def test_a_trial_still_refuses_an_unsearchable_role(self, tmp_path):
        seam, _ctx, _base = search_seam(tmp_path)
        with pytest.raises(RuntimeError) as exc:
            seam({"src.x": "poison"})
        assert "which a search may never re-tune" in str(exc.value)
        assert "'src.x'" in str(exc.value)
        assert "fingerprinted identity" in str(exc.value)

    def test_the_winner_pass_refuses_an_unsearchable_role_too(self, tmp_path):
        seam, ctx, _base = search_seam(tmp_path)
        with pytest.raises(RuntimeError) as exc:
            seam.apply_winner({"src.x": 9}, ctx, {})
        assert "which a search may never re-tune" in str(exc.value)

    def test_an_undeclared_node_still_refuses_before_the_role_is_read(
        self, tmp_path
    ):
        seam, _ctx, _base = search_seam(tmp_path)
        with pytest.raises(RuntimeError) as exc:
            seam({"ghost.x": 1})
        assert "addressing a declared node" in str(exc.value)


class TestSearchBehaviourIsUnchanged:
    def test_the_parabolas_trials_and_winner_are_what_they_always_were(
        self, tmp_path
    ):
        # The same numbers tests/pipeline/test_kinds_search.py asserts: the
        # extraction must not move a single one of them.
        result = run_document(
            parabola_document(tmp_path), asof=ASOF, registry=search_registry()
        )
        assert result.state == "ran"
        assert result.outputs["search"]["best_params"] == {"theta.theta": 3.0}
        assert result.outputs["search"]["best_score"] == 0.0
        assert result.outputs["search"]["trials"] == [
            {"overrides": {"theta.theta": 0.0}, "score": 9.0},
            {"overrides": {"theta.theta": 3.0}, "score": 0.0},
            {"overrides": {"theta.theta": 5.0}, "score": 4.0},
        ]
        assert result.outputs["theta"]["value"] == 3.0
        assert result.outputs["val"]["metrics"]["loss"] == 0.0

    def test_a_probe_kinds_bad_override_is_still_a_loud_trial_error(self, tmp_path):
        pipeline = parabola_pipeline(
            search_uses="tests.pipeline.test_kinds_search:ProbeSearch",
            search_params={
                "space": {"theta.theta": [0.0]},
                "objective": "$val.metrics.loss",
                "probe": {"nope": 1},
            },
        )
        result = run_document(
            parabola_document(tmp_path, pipeline=pipeline),
            asof=ASOF,
            registry=search_registry(),
        )
        assert result.state == "error"
        assert "'<node>.<param.path>'" in result.error
        assert "{'nope': 1}" in result.error


# ---------------------------------------------------------------------------
# The runner never widens the ordinary lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    def one_node_graph(self, tmp_path, uses):
        pipeline = {
            "src": NodeSpec(uses=HERE + "Src", params={"x": 1}),
            "sink": NodeSpec(uses=uses, inputs={"x": "$src.x"}),
        }
        the_plan = plan(PipelineDocument(name="runner", pipeline=pipeline))
        ctx = a_ctx(tmp_path)
        base = {"src": {"x": 1}, "sink": {"x": 1}}
        return the_plan, ctx, base

    def test_validate_inputs_problems_stop_the_pass(self, tmp_path):
        the_plan, ctx, base = self.one_node_graph(tmp_path, HERE + "Picky")
        runner = a_runner(the_plan, set(the_plan.order), base)
        with pytest.raises(ConfigError) as exc:
            runner.rerun({"src.x": "poison"}, dict(base), ctx, {})
        assert "x must not be 'poison'" in str(exc.value)

    def test_an_output_contract_violation_stops_the_pass(self, tmp_path):
        the_plan, ctx, base = self.one_node_graph(tmp_path, HERE + "BadContract")
        runner = a_runner(the_plan, set(the_plan.order), base)
        with pytest.raises(ConfigError) as exc:
            runner.rerun({"src.x": 2}, dict(base), ctx, {})
        assert "declared contract" in str(exc.value)
