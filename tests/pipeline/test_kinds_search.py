"""``hpo-grid`` + the driver's subgraph re-execution seam (docs/24 §8, §10.3).

Three layers, mirroring the seam's contract:

* unit — grid enumeration, sha256 subsampling, tie-breaking, param
  validation, override-path application, registration;
* planner — the winner-consistency rule (stale consumers refuse to
  plan) and space-path validation;
* driver — end-to-end ``run_document`` with PRIVATE registries: a
  parabola fixture whose argmin is analytic (the winner is unambiguous
  by construction), and the synthetic market where every search trial
  must EQUAL a from-scratch pinned run of the same document.
"""

import copy
import json
import os
import re

import pytest

from dskit.pipeline.base import (
    ConfigError,
    OutputsConfig,
    SinkConfig,
    TimeSplitConfig,
    TrackingConfig,
)
from dskit.pipeline.document import (
    _NODE_KEY_OK,
    NodeSpec,
    PipelineDocument,
    flatten_param_paths,
)
from dskit.pipeline.driver import _apply_param_override, run_document
from dskit.pipeline.kinds_search import (
    HpoGrid, TopTrials, _grid, _subsample, register,
)
from dskit.pipeline.kinds_search import _is_json_scalar as _grid_is_json_scalar
from dskit.pipeline.node import Node, NodeContext, NodeKindRegistry
from dskit.pipeline.planner import _is_json_scalar as _planner_is_json_scalar
from dskit.pipeline.planner import plan
from dskit.pipeline.synthetic_nodes import (
    SynthClip,
    SynthEvents,
    SynthLabels,
    SynthMarketSignal,
    SynthReport,
    SynthScore,
    SynthSearch,
    SynthTrain,
)
from dskit.pipeline.testing import MemoryTracker
from tests.pipeline.dochelpers import DAY

ASOF = "2026-01-01"

#: Trivially-valid splits for score nodes that never read ctx.splits.
FLAT_SPLITS = TimeSplitConfig(train_end_ms=1, val_end_ms=2, test_end_ms=3)


# ---------------------------------------------------------------------------
# The parabola fixture — custom nodes with an ANALYTIC argmin
# ---------------------------------------------------------------------------


class ConstSource(Node):
    """Literal data source (role ``data``) — the clean upstream that must
    never be re-executed by trials."""

    role = "data"
    outputs = ("x",)

    def fingerprint(self):
        return {"kind": "const", "x": self.params.get("x", 0)}

    def run(self, ctx, inputs):
        return {"x": self.params.get("x", 0)}


class ThetaTrain(Node):
    """One trainable knob (role ``train``): value = theta + seed/100 +
    opt.bias. The seed term makes the seeds ensemble observable; the
    nested ``opt`` block exercises dotted override paths."""

    role = "train"
    outputs = ("value",)

    def validate_inputs(self, inputs):
        return ["x must not be 'poison'"] if inputs.get("x") == "poison" else []

    def run(self, ctx, inputs):
        if self.params.get("misbehave"):
            return {"wrong": 1}
        opt = self.params.get("opt") or {}
        return {
            "value": self.params["theta"]
            + self.params.get("seed", 0) / 100
            + opt.get("bias", 0.0)
        }


class PassThrough(Node):
    """Pointwise relay (role ``transform``) — a LEGAL search-space head
    sitting between the data node (identity, off-limits to spaces) and
    the trainable, so trials can poison theta's upstream lawfully."""

    role = "transform"
    outputs = ("x",)

    def run(self, ctx, inputs):
        emit = self.params.get("emit")
        return {"x": inputs["x"] if emit is None else emit}


class ParabolaScore(Node):
    """metrics.loss = (value - center)^2 (role ``score``): the val loss is
    minimized at value == center — the grid winner is unambiguous."""

    role = "score"
    outputs = ("metrics",)

    @classmethod
    def validate_params(cls, params):
        if params.get("split") not in ("train", "val", "test"):
            return [f"split must be declared, got {params.get('split')!r}"]
        return []

    def run(self, ctx, inputs):
        loss = (inputs["value"] - self.params.get("center", 3.0)) ** 2
        return {"metrics": {"loss": loss, "n": 1}}


class CountGate(Node):
    """Verdict = GO iff value >= bar (role ``gate``) — the winner-pass
    verdict-flip guard's test subject."""

    role = "gate"
    outputs = ("n", "verdict")

    def run(self, ctx, inputs):
        n = inputs["value"]
        return {"n": n, "verdict": "GO" if n >= self.params.get("bar", 0) else "NO-GO"}


class ProbeSearch(Node):
    """A custom search kind driving the seam by hand: calls ``ctx.rerun``
    on its ``probe`` overrides once, then returns whatever ``winner`` its
    params pin (default: none) — the scratch-isolation and custom-winner
    test subject."""

    role = "search"
    outputs = ("best_params", "best_score", "trials")

    def run(self, ctx, inputs):
        probe = self.params.get("probe", {"theta.theta": 0.0})
        score = ctx.rerun(probe)  # passed through uncoerced — the seam validates
        return {
            "best_params": dict(self.params.get("winner", {})),
            "best_score": score,
            "trials": [{"overrides": probe, "score": score}],
        }


THETA = "tests.pipeline.test_kinds_search:ThetaTrain"


def parabola_pipeline(theta_params=None, search_params=None, search_uses="hpo-grid"):
    base_search = {
        "space": {"theta.theta": [0.0, 3.0, 5.0]},
        "objective": "$val.metrics.loss",
        "select": "min",
    }
    base_search.update(search_params or {})
    return {
        "src": NodeSpec(
            uses="tests.pipeline.test_kinds_search:ConstSource", params={"x": 1}
        ),
        "theta": NodeSpec(
            uses=THETA,
            inputs={"x": "$src.x"},
            params=dict(theta_params or {"theta": 10.0}),
        ),
        "val": NodeSpec(
            uses="tests.pipeline.test_kinds_search:ParabolaScore",
            inputs={"value": "$theta.value"},
            params={"split": "val", "center": 3.0},
        ),
        "search": NodeSpec(uses=search_uses, params=base_search),
        "rep": NodeSpec(
            uses="synth-report",
            inputs={
                "best_score": "$search.best_score",
                "value": "$theta.value",
                "loss": "$val.metrics.loss",
            },
        ),
    }


def parabola_document(tmp_path, pipeline=None, **overrides):
    base = {
        "name": "parabola",
        "pipeline": pipeline or parabola_pipeline(),
        "splits": FLAT_SPLITS,
        "outputs": OutputsConfig(run_root=str(tmp_path)),
    }
    base.update(overrides)
    return PipelineDocument(**base)


def search_registry():
    """The PRIVATE registry: needed synthetic kinds registered one by
    one, plus ``hpo-grid`` via its own :func:`register`."""
    reg = NodeKindRegistry()
    reg.register("synth-events", SynthEvents)
    reg.register("synth-labels", SynthLabels)
    reg.register("synth-clip", SynthClip)
    reg.register("synth-market", SynthMarketSignal)
    reg.register("synth-train", SynthTrain)
    reg.register("synth-score", SynthScore)
    reg.register("synth-report", SynthReport)
    register(reg)
    return reg


@pytest.fixture
def registry():
    return search_registry()


def read_json(run_dir, *parts):
    with open(os.path.join(run_dir, *parts), encoding="utf-8") as fh:
        return json.load(fh)


class FakeRerun:
    """A hand-rolled seam for unit tests: records call order, returns
    ``fn(overrides)``, and exposes ``seed_targets`` like the driver's."""

    def __init__(self, fn, seed_targets=()):
        self.fn = fn
        self.calls = []
        self.seed_targets = tuple(seed_targets)

    def __call__(self, overrides):
        self.calls.append(dict(overrides))
        return self.fn(overrides)


def grid_ctx(tmp_path, rerun):
    return NodeContext(name="t", asof=ASOF, run_dir=str(tmp_path), rerun=rerun)


def grid_node(**params):
    base = {"space": {"a.x": [10, 20], "b.y": [1, 2]}, "objective": 0.0}
    base.update(params)
    return HpoGrid("search", base)


# ---------------------------------------------------------------------------
# Unit: validate_params
# ---------------------------------------------------------------------------


class TestValidateParams:
    def good(self, **over):
        params = {
            "space": {"train.lr": [1e-4, 3e-4]},
            "objective": "$validate.metrics.loss",
        }
        params.update(over)
        return params

    def test_the_spec_example_shape_passes(self):
        assert (
            HpoGrid.validate_params(
                self.good(select="min", n_trials=12, seeds=[0, 1], seed=3)
            )
            == []
        )

    def test_materialized_objective_is_tolerated_at_construction(self):
        # The driver materializes "$validate.metrics.loss" into the base
        # pass's float before constructing the node — construction must
        # not refuse it.
        assert HpoGrid.validate_params(self.good(objective=49.0)) == []
        HpoGrid("search", self.good(objective=49.0))

    @pytest.mark.parametrize("bad", [None, {}, [], "x"])
    def test_space_must_be_a_non_empty_dict(self, bad):
        problems = HpoGrid.validate_params(self.good(space=bad))
        assert any("space must be a non-empty dict" in p for p in problems)

    @pytest.mark.parametrize("key", ["train", "Train.lr", "9x.lr", ""])
    def test_space_keys_must_be_node_dot_param(self, key):
        problems = HpoGrid.validate_params(self.good(space={key: [1]}))
        assert any("must be '<node>.<param.path>'" in p for p in problems)

    @pytest.mark.parametrize("values", [[], (), 5, "abc"])
    def test_space_values_must_be_non_empty_lists(self, values):
        problems = HpoGrid.validate_params(self.good(space={"train.lr": values}))
        assert any("non-empty list" in p for p in problems)

    @pytest.mark.parametrize("scalar", [[{"a": 1}], [[1]], [float("inf")]])
    def test_space_values_must_be_json_scalars(self, scalar):
        problems = HpoGrid.validate_params(self.good(space={"train.lr": scalar}))
        assert any("JSON scalars" in p for p in problems)

    @pytest.mark.parametrize(
        "spec",
        [
            {"low": 0.0, "high": 1.0},
            {"low": 1e-4, "high": 1e-1, "log": True},
            {"low": 1, "high": 8, "int": True},
        ],
    )
    def test_a_continuous_range_spec_is_refused(self, spec):
        # The optuna pack's range-spec form, offered to the grid: REFUSED,
        # deliberately and forever — exhaustive enumeration over a real
        # interval is meaningless, and `itertools.product` over a dict
        # would silently enumerate its KEYS ('low', 'high') as if they
        # were grid values. The planner no longer stands in the way (it
        # owns the structural space rules and passes any non-empty dict
        # through to the kind), so this refusal is the only guard left.
        problems = HpoGrid.validate_params(self.good(space={"train.lr": spec}))
        assert any("non-empty list of JSON scalars" in p for p in problems)

    def test_a_range_spec_cannot_construct_the_grid(self):
        # Same rule at the other gate: a search node's $-ref objective
        # defers plan-time validate_params, so CONSTRUCTION is where a
        # document carrying a range spec dies — before any enumeration.
        params = self.good(space={"train.lr": {"low": 0.0, "high": 1.0}})
        with pytest.raises(ConfigError, match="non-empty list of JSON scalars"):
            HpoGrid("search", params)

    def test_objective_required_and_ref_shaped(self):
        params = self.good()
        del params["objective"]
        assert any(
            "objective is required" in p for p in HpoGrid.validate_params(params)
        )
        problems = HpoGrid.validate_params(self.good(objective="loss"))
        assert any("'$node.path' reference" in p for p in problems)

    def test_select_min_max_only(self):
        assert HpoGrid.validate_params(self.good(select="best"))
        assert HpoGrid.validate_params(self.good(select="max")) == []

    @pytest.mark.parametrize("bad", [0, -1, True, 1.5])
    def test_n_trials_must_be_a_positive_int(self, bad):
        assert any(
            "n_trials" in p for p in HpoGrid.validate_params(self.good(n_trials=bad))
        )

    @pytest.mark.parametrize("bad", [-1, True, "0"])
    def test_seed_must_be_a_non_negative_int(self, bad):
        assert any("seed" in p for p in HpoGrid.validate_params(self.good(seed=bad)))

    @pytest.mark.parametrize("bad", [[], [0.5], [True], "01", [1, "2"]])
    def test_seeds_must_be_a_non_empty_int_list(self, bad):
        assert any("seeds" in p for p in HpoGrid.validate_params(self.good(seeds=bad)))


# ---------------------------------------------------------------------------
# Unit: grid mechanics
# ---------------------------------------------------------------------------


class TestGridMechanics:
    def test_enumeration_sorted_keys_given_value_order(self):
        assert _grid({"b.y": [1, 2], "a.x": [10, 20]}) == [
            {"a.x": 10, "b.y": 1},
            {"a.x": 10, "b.y": 2},
            {"a.x": 20, "b.y": 1},
            {"a.x": 20, "b.y": 2},
        ]

    def test_run_evaluates_in_enumeration_order(self, tmp_path):
        fake = FakeRerun(lambda o: float(o["a.x"] + o["b.y"]))
        out = grid_node().run(grid_ctx(tmp_path, fake), {})
        assert fake.calls == _grid({"a.x": [10, 20], "b.y": [1, 2]})
        assert [t["overrides"] for t in out["trials"]] == fake.calls
        assert out["best_params"] == {"a.x": 10, "b.y": 1}
        assert out["best_score"] == 11.0

    def test_select_max_picks_the_other_end(self, tmp_path):
        fake = FakeRerun(lambda o: float(o["a.x"] + o["b.y"]))
        out = grid_node(select="max").run(grid_ctx(tmp_path, fake), {})
        assert out["best_params"] == {"a.x": 20, "b.y": 2}
        assert out["best_score"] == 22.0

    @pytest.mark.parametrize("select", ["min", "max"])
    def test_ties_go_to_the_first_enumerated(self, tmp_path, select):
        out = grid_node(select=select).run(
            grid_ctx(tmp_path, FakeRerun(lambda o: 1.0)), {}
        )
        assert out["best_params"] == {"a.x": 10, "b.y": 1}

    def test_without_the_driver_seam_run_refuses(self, tmp_path):
        ctx = NodeContext(name="t", asof=ASOF, run_dir=str(tmp_path))
        with pytest.raises(RuntimeError, match="hpo-grid runs under the driver"):
            grid_node().run(ctx, {})

    def test_n_trials_at_or_above_grid_size_is_exhaustive(self, tmp_path):
        fake = FakeRerun(lambda o: 0.0)
        out = grid_node(n_trials=4).run(grid_ctx(tmp_path, fake), {})
        assert len(out["trials"]) == 4
        out = grid_node(n_trials=99).run(grid_ctx(tmp_path, fake), {})
        assert len(out["trials"]) == 4

    def test_subsample_is_deterministic_and_order_preserving(self):
        trials = _grid({"a.x": list(range(8))})
        picked = _subsample(trials, 3, 0)
        assert picked == _subsample(trials, 3, 0)
        assert len(picked) == 3
        positions = [trials.index(t) for t in picked]
        assert positions == sorted(positions)  # enumeration order survives
        assert all(t in trials for t in picked)

    def test_subsample_pinned_selection(self):
        # The documented rule frozen: sha256(f"{seed}:{canonical}") ranks,
        # smallest digests win. sha256 never changes, so neither may this.
        trials = _grid({"a.x": list(range(8))})
        assert _subsample(trials, 3, 0) == [{"a.x": 1}, {"a.x": 3}, {"a.x": 7}]
        assert _subsample(trials, 3, 1) == [{"a.x": 0}, {"a.x": 2}, {"a.x": 3}]

    def test_seeds_average_and_apply_only_to_seed_targets(self, tmp_path):
        fake = FakeRerun(
            lambda o: float(o["a.x"] + o.get("t.seed", 0)),
            seed_targets=("t.seed",),
        )
        out = grid_node(space={"a.x": [10, 20]}, seeds=[0, 4]).run(
            grid_ctx(tmp_path, fake), {}
        )
        # Every trial evaluated once per seed, seed override attached.
        assert [c.get("t.seed") for c in fake.calls] == [0, 4, 0, 4]
        assert out["trials"][0] == {
            "overrides": {"a.x": 10},
            "score": 12.0,
            "seed_scores": [10.0, 14.0],
        }
        # best_params carries GRID overrides only — never a trial seed.
        assert out["best_params"] == {"a.x": 10}
        assert out["best_score"] == 12.0

    def test_seeds_without_targets_still_ensemble(self, tmp_path):
        fake = FakeRerun(lambda o: float(o["a.x"]))
        out = grid_node(space={"a.x": [10]}, seeds=[0, 1, 2]).run(
            grid_ctx(tmp_path, fake), {}
        )
        assert len(fake.calls) == 3
        assert out["trials"][0]["seed_scores"] == [10.0, 10.0, 10.0]


class TestNonFiniteObjective:
    """S1 #3: a non-finite objective must be refused BY NAME when the
    trial reports. NaN defeats every strict comparison (``x < nan`` and
    ``x > nan`` are both False), so a FIRST-trial NaN would silently
    stick as "best" and the driver would then apply its overrides as the
    winner; an inf outranks every real trial the same way."""

    def test_a_first_trial_nan_cannot_win(self, tmp_path):
        # a.x=10, b.y=1 is the first enumerated trial — exactly the one
        # whose NaN sticks under the strict-compare winner loop.
        fake = FakeRerun(lambda o: float("nan") if o["a.x"] == 10 else 1.0)
        with pytest.raises(ValueError) as excinfo:
            grid_node().run(grid_ctx(tmp_path, fake), {})
        message = str(excinfo.value)
        assert "non-finite objective" in message
        assert "'a.x': 10" in message  # the trial is named by its overrides
        assert "nan" in message

    def test_a_mid_run_nan_is_refused_not_ranked(self, tmp_path):
        fake = FakeRerun(
            lambda o: float("nan") if (o["a.x"], o["b.y"]) == (20, 1) else 1.0
        )
        with pytest.raises(ValueError, match="non-finite objective"):
            grid_node().run(grid_ctx(tmp_path, fake), {})

    @pytest.mark.parametrize("bad", [float("inf"), float("-inf")])
    def test_infinities_are_refused_too(self, tmp_path, bad):
        fake = FakeRerun(lambda o: bad if o["a.x"] == 10 else 1.0)
        with pytest.raises(ValueError, match="non-finite objective"):
            grid_node().run(grid_ctx(tmp_path, fake), {})

    def test_a_nan_seed_score_names_the_whole_trial(self, tmp_path):
        # the seeds ensemble reports once per seed; the refusal names the
        # exact rerun call (grid overrides plus the seed override), not
        # just the grid point — the seed is part of what failed.
        fake = FakeRerun(
            lambda o: float("nan") if o.get("t.seed") == 4 else 1.0,
            seed_targets=("t.seed",),
        )
        with pytest.raises(ValueError) as excinfo:
            grid_node(space={"a.x": [10]}, seeds=[0, 4]).run(
                grid_ctx(tmp_path, fake), {}
            )
        message = str(excinfo.value)
        assert "non-finite objective" in message
        assert "'t.seed': 4" in message


class TestRegister:
    def test_registers_unowned_and_skips_if_present(self):
        reg = NodeKindRegistry()
        register(reg)
        assert reg.get("hpo-grid") == (HpoGrid, False)
        assert reg.get("top-trials") == (TopTrials, False)
        register(reg)  # idempotent — no duplicate-name raise
        assert reg.get("hpo-grid") == (HpoGrid, False)
        assert reg.get("top-trials") == (TopTrials, False)

    def test_never_shadows_an_existing_claim(self):
        reg = NodeKindRegistry()
        reg.register("hpo-grid", SynthSearch)
        register(reg)
        assert reg.get("hpo-grid") == (SynthSearch, False)
        assert reg.get("top-trials") == (TopTrials, False)


class TestTopTrials:
    def test_keeps_the_top_fraction_and_assigns_fresh_seeds(self):
        out = TopTrials(
            "ensemble", {"frac": 0.5, "size": 3, "seed": 7, "select": "min"},
        ).run(None, {"trials": [
            {"overrides": {"m.lr": 0.1}, "score": 1.0},
            {"overrides": {"m.lr": 0.2}, "score": 0.5},
            {"overrides": {"m.lr": 0.3}, "score": 2.0},
            {"overrides": {"m.lr": 0.4}, "score": 0.25},
        ]})
        assert out["metrics"]["n_pool"] == 2.0
        assert out["metrics"]["n_members"] == 3.0
        assert {row["seed"] for row in out["members"]} == {7, 8, 9}
        assert all(row["score"] in (0.25, 0.5) for row in out["members"])


class TestApplyOverride:
    def test_sets_nested_existing_keys(self):
        params = {"opt": {"lr": 0.1}, "n": 1}
        _apply_param_override(params, "t", ("opt", "lr"), 0.9)
        _apply_param_override(params, "t", ("n",), 2)
        assert params == {"opt": {"lr": 0.9}, "n": 2}

    def test_missing_terminal_key_is_an_error(self):
        with pytest.raises(ValueError, match="never create them"):
            _apply_param_override({"opt": {"lr": 0.1}}, "t", ("opt", "momentum"), 0.9)

    def test_missing_intermediate_key_is_an_error(self):
        with pytest.raises(ValueError, match="no 'ghost' to descend into"):
            _apply_param_override({"opt": {}}, "t", ("ghost", "lr"), 0.9)

    def test_non_dict_cursor_is_an_error(self):
        with pytest.raises(ValueError, match="not an existing param"):
            _apply_param_override({"opt": 5}, "t", ("opt", "lr"), 0.9)


class TestSpaceKeyGrammarParity:
    """``flatten_param_paths`` (what the tracker logs) and the space-key
    grammar (what ``hpo-grid`` tunes) must be the SAME spelling.

    Two writers of one grammar is exactly the duplication that diverges,
    so nothing here restates an expected key list: every key the flattener
    emits is fed BACK to ``HpoGrid``'s space validation and to the
    driver's override resolver, and must address the very leaf it came
    from.
    """

    #: One node's params covering every shape the walk can meet — and the
    #: whole SEGMENT alphabet: ``T_max``/``useAmp`` (uppercase head and
    #: inside), ``beta2`` (digit tail), ``_warm`` (underscore head) pin
    #: that neither grammar quietly narrows its segment class — a fixture
    #: of plain lowercase words would let letters-only or no-underscore
    #: mutations of either regex pass unseen (round-5 finding 2).
    PARAMS = {
        "hidden_size": 32,
        "lr": 0.001,
        "monitor": "val_loss",
        "opt": {
            "kind": "adam",
            "beta2": 0.999,
            "_warm": 5,
            "sched": {"warmup": 10, "T_max": 100},
        },
        "cuts": [1, 2],
        "empty": {},
        "useAmp": True,
        "bankroll": {"$prev": "size.final_bankroll", "default": 1000.0},
    }

    def test_every_flattened_key_is_a_space_key_hpo_grid_accepts(self):
        flat = flatten_param_paths("qhat", self.PARAMS)
        assert flat  # a vacuous loop would prove nothing
        HpoGrid(
            "search",
            {
                "space": {key: [1, 2] for key in flat},
                "objective": "$validate.metrics.loss",
            },
        )

    @staticmethod
    def _leaf_paths(value, prefix=()):
        """Every path to a KNOB leaf, walked NAIVELY — no grammar applied.

        Deliberate independent restatement: an assertion sourced from the
        flattener would assert nothing, so this walk descends every
        non-empty dict itself and lets the grammar (not the walk) do the
        filtering — stopping only at a carry (a dict holding a ``$prev``
        key), which the round-4 ruling makes wiring: one leaf, never a
        subtree of knobs."""
        if isinstance(value, dict) and value and "$prev" not in value:
            for name, inner in value.items():
                yield from TestSpaceKeyGrammarParity._leaf_paths(inner, (*prefix, name))
        else:
            yield prefix

    def test_every_tunable_leaf_is_emitted(self):
        # The CONVERSE pin. Forward-only ("every emitted key is legal") goes
        # green when the flattener emits nothing, so it cannot see an
        # under-emission: a knob hpo-grid tunes and the driver overrides,
        # missing from the payload, is the very 'cannot filter runs by lr'
        # gap this logging exists to close.
        flat = flatten_param_paths("qhat", self.PARAMS)
        checked = 0
        for path in self._leaf_paths(self.PARAMS):
            key = ".".join(("qhat", *path))
            if HpoGrid.validate_params(
                {"space": {key: [1]}, "objective": "$validate.metrics.loss"}
            ):
                continue  # the space grammar refuses this key: not tunable
            target = copy.deepcopy(self.PARAMS)
            _apply_param_override(target, "qhat", path, "SENTINEL")
            checked += 1
            assert key in flat, f"{key} is tunable and overridable but unlogged"
        assert checked >= 6  # a vacuous loop would prove nothing

    def test_every_flattened_key_resolves_to_the_leaf_it_came_from(self):
        flat = flatten_param_paths("qhat", self.PARAMS)
        for key in flat:
            node, _, path = key.partition(".")
            target = copy.deepcopy(self.PARAMS)
            _apply_param_override(target, node, path.split("."), "SENTINEL")
            after = flatten_param_paths(node, target)
            assert after[key] == "SENTINEL"
            assert self._unrelated(after, key) == self._unrelated(flat, key)

    @staticmethod
    def _unrelated(mapping, key):
        """``mapping`` minus everything on ``key``'s own branch.

        An override touches its target, whatever was BELOW it (replaced),
        and the blocks ABOVE it that are logged whole — every one of those
        moves is the override doing its job. Nothing off that branch may
        move, which is the pin."""
        return {
            k: v
            for k, v in mapping.items()
            if k != key
            and not k.startswith(f"{key}.")
            and not key.startswith(f"{k}.")
        }

    def test_node_keys_across_their_whole_alphabet_head_legal_space_keys(self):
        # Round-4 ruling (finding 6): PARAMS pins the SEGMENT half of the
        # grammar; the NODE-KEY half rested on an unpinned subset relation
        # between document._NODE_KEY_OK and _SPACE_KEY_OK's head. Walk the
        # node-name alphabet's edges — single letter, underscore head,
        # digit+underscore tail — through BOTH authorities, so either
        # regex narrowing fails here.
        for name in ("a", "_x", "z9_"):
            assert re.match(_NODE_KEY_OK, name), name  # a legal node key…
            flat = flatten_param_paths(name, {"lr": 0.001, "opt": {"beta": 0.9}})
            assert len(flat) == 2  # …flattens (a vacuous space proves nothing)…
            HpoGrid(  # …to keys hpo-grid's own space validation accepts
                "search",
                {
                    "space": {key: [1, 2] for key in flat},
                    "objective": "$validate.metrics.loss",
                },
            )
        # And a head the document refuses can never head a space key either.
        assert not re.match(_NODE_KEY_OK, "Q")
        assert HpoGrid.validate_params(
            {"space": {"Q.lr": [1]}, "objective": "$validate.metrics.loss"}
        )


# ---------------------------------------------------------------------------
# Planner: the winner-consistency rule + space-path validation
# ---------------------------------------------------------------------------


class TestScalarRuleAgreement:
    """The 'JSON scalar' line is drawn in TWO modules — the tier-1 engine
    (``planner``, which may not import a kind) and ``kinds_search`` — so
    the agreement is pinned, not assumed."""

    #: (value, is-a-JSON-scalar) — restated INDEPENDENTLY of both
    #: implementations, so a matching drift in both still fails here.
    CASES = (
        (None, True),
        (True, True),
        (False, True),
        (0, True),
        (-2, True),
        (1.5, True),
        ("", True),
        ("s", True),
        (float("nan"), False),
        (float("inf"), False),
        ([], False),
        ([1], False),
        ({}, False),
        ({"low": 0.0, "high": 1.0}, False),
        ((1,), False),
    )

    def test_both_gates_draw_the_same_line(self):
        # A document that passed the plan-time check and then died at the
        # kind's construction check (or the reverse) would be this rule
        # having two meanings; the two refusals must be the same refusal.
        for value, expected in self.CASES:
            assert _planner_is_json_scalar(value) is expected, value
            assert _grid_is_json_scalar(value) is expected, value


class TestPlannerRules:
    def test_parabola_document_plans(self, tmp_path, registry):
        plan(parabola_document(tmp_path), registry)

    def test_stale_consumer_refuses_to_plan(self, tmp_path, registry):
        # 'leech' consumes the tuned node's output but is neither
        # re-executed with the winner nor downstream of the search — it
        # would consume the stale pre-winner pass.
        pipeline = parabola_pipeline()
        pipeline["leech"] = NodeSpec(
            uses="synth-report", inputs={"value": "$theta.value"}
        )
        with pytest.raises(ConfigError, match=r"\['leech'\].*stale pre-winner"):
            plan(parabola_document(tmp_path, pipeline=pipeline), registry)

    def test_wiring_the_consumer_behind_the_search_cures_it(self, tmp_path, registry):
        pipeline = parabola_pipeline()
        pipeline["leech"] = NodeSpec(
            uses="synth-report",
            inputs={"value": "$theta.value", "best": "$search.best_params"},
        )
        plan(parabola_document(tmp_path, pipeline=pipeline), registry)

    def test_offender_list_names_all_and_only_offenders(self, tmp_path, registry):
        pipeline = parabola_pipeline()
        pipeline["leech_a"] = NodeSpec(
            uses="synth-report", inputs={"value": "$theta.value"}
        )
        pipeline["leech_b"] = NodeSpec(
            uses="synth-report", inputs={"loss": "$val.metrics.loss"}
        )
        with pytest.raises(ConfigError) as exc:
            plan(parabola_document(tmp_path, pipeline=pipeline), registry)
        message = str(exc.value)
        assert "['leech_a', 'leech_b']" in message
        assert "'rep'" not in message  # rep is downstream of the search: fine

    def test_literal_objective_refused_at_plan(self, tmp_path, registry):
        pipeline = parabola_pipeline(search_params={"objective": 5.0})
        with pytest.raises(ConfigError, match=r"must be a \$-reference"):
            plan(parabola_document(tmp_path, pipeline=pipeline), registry)

    def test_space_head_param_must_exist(self, tmp_path, registry):
        pipeline = parabola_pipeline(search_params={"space": {"theta.ghost": [1]}})
        with pytest.raises(ConfigError, match="declares no param 'ghost'"):
            plan(parabola_document(tmp_path, pipeline=pipeline), registry)

    def test_space_key_needs_a_param_path(self, tmp_path, registry):
        pipeline = parabola_pipeline(search_params={"space": {"theta": [1]}})
        with pytest.raises(ConfigError, match="name the param to override"):
            plan(parabola_document(tmp_path, pipeline=pipeline), registry)

    def test_space_undeclared_node_still_refused(self, tmp_path, registry):
        pipeline = parabola_pipeline(search_params={"space": {"ghost.lr": [1]}})
        with pytest.raises(ConfigError, match="no node 'ghost'"):
            plan(parabola_document(tmp_path, pipeline=pipeline), registry)

    def test_space_values_validated(self, tmp_path, registry):
        pipeline = parabola_pipeline(search_params={"space": {"theta.theta": []}})
        with pytest.raises(ConfigError, match="non-empty list"):
            plan(parabola_document(tmp_path, pipeline=pipeline), registry)
        pipeline = parabola_pipeline(search_params={"space": {"theta.theta": [[1, 2]]}})
        with pytest.raises(ConfigError, match="JSON scalars"):
            plan(parabola_document(tmp_path, pipeline=pipeline), registry)
        # An EMPTY dict refuses at PLAN too — the planner's pass-through
        # of the range form starts past non-emptiness, so `{}` never
        # reaches any kind's grammar (which defers to execute anyway).
        # No kind may assign `{}` a meaning without a planner change.
        pipeline = parabola_pipeline(search_params={"space": {"theta.theta": {}}})
        with pytest.raises(ConfigError, match="non-empty range-spec dict"):
            plan(parabola_document(tmp_path, pipeline=pipeline), registry)

    def test_missing_space_refused_at_plan(self, tmp_path, registry):
        spec = NodeSpec(uses="hpo-grid", params={"objective": "$val.metrics.loss"})
        pipeline = parabola_pipeline()
        pipeline["search"] = spec
        with pytest.raises(ConfigError, match="space must be a non-empty dict"):
            plan(parabola_document(tmp_path, pipeline=pipeline), registry)

    def test_the_space_refusal_names_both_value_forms(self, tmp_path, registry):
        # The refusal a user misreads first: it is the planner's ONE
        # statement of the space rule that is not a comment, so it must
        # name the same two value forms the planner actually accepts
        # (list OR range dict) — a message saying only 'list of values'
        # sends an author writing the continuous form looking for a bug
        # that is not there.
        spec = NodeSpec(
            uses="hpo-grid",
            params={"objective": "$val.metrics.loss", "space": ["theta.theta"]},
        )
        pipeline = parabola_pipeline()
        pipeline["search"] = spec
        with pytest.raises(ConfigError) as excinfo:
            plan(parabola_document(tmp_path, pipeline=pipeline), registry)
        message = str(excinfo.value)
        assert "non-empty list of JSON scalars" in message
        assert "range-spec dict" in message

    def test_a_search_node_defers_plan_time_validate_params(self, tmp_path, registry):
        # The fact the whole division of labour rests on, pinned: a search
        # node's `objective` is a $-reference BY CONTRACT (spec §8), so its
        # params carry an unresolved ref and plan() DEFERS the kind's
        # validate_params to execute (planner._has_unresolved_ref). The
        # deferral is ref-driven, not kind-driven — strip every ref from a
        # search node's params and the kind's grammar runs at plan — but
        # under the contract the refusal below lands during the run.
        the_plan = plan(parabola_document(tmp_path), registry)
        assert "search" in the_plan.deferred_params
        assert the_plan.to_obj()["nodes"]["search"]["params_validation"] == "deferred"

    def test_a_range_spec_document_dies_before_a_grid_trial_runs(
        self, tmp_path, registry
    ):
        # The division of labour, pinned end to end: the planner accepts
        # the range-spec SHAPE (it is the optuna kind's grammar, and the
        # planner may not know about kinds), the document plans, and the
        # run then refuses at hpo-grid's construction naming the offending
        # space key — no trial is ever enumerated over an interval.
        # The cost is real and deliberate, not an oversight: because the
        # search node's params defer (above), this refusal lands AFTER the
        # upstream nodes have executed, so a run dir exists by then.
        pipeline = parabola_pipeline(
            search_params={"space": {"theta.theta": {"low": 0.0, "high": 5.0}}}
        )
        document = parabola_document(tmp_path, pipeline=pipeline)
        plan(document, registry)
        result = run_document(document, asof=ASOF, registry=registry)
        assert result.state == "error"
        assert "must be a non-empty list of JSON scalars" in result.error
        assert "'theta.theta'" in result.error

    def test_deep_path_segments_defer_to_execute(self, tmp_path, registry):
        # Head key 'opt' exists; 'momentum' below it is only checkable at
        # execute (the planner sees params, not their nesting semantics).
        pipeline = parabola_pipeline(
            theta_params={"theta": 10.0, "opt": {"bias": 0.5}},
            search_params={"space": {"theta.opt.momentum": [0.9]}},
        )
        plan(parabola_document(tmp_path, pipeline=pipeline), registry)


# ---------------------------------------------------------------------------
# Driver: end to end on the parabola (analytic winner)
# ---------------------------------------------------------------------------


class TestEndToEndParabola:
    def test_winner_semantics_records_and_artifacts(self, tmp_path, registry):
        result = run_document(parabola_document(tmp_path), asof=ASOF, registry=registry)
        assert result.state == "ran"
        # The analytic argmin of (theta - 3)^2 over {0, 3, 5} is 3.
        search = result.outputs["search"]
        assert search["best_params"] == {"theta.theta": 3.0}
        assert search["best_score"] == 0.0
        assert search["trials"] == [
            {"overrides": {"theta.theta": 0.0}, "score": 9.0},
            {"overrides": {"theta.theta": 3.0}, "score": 0.0},
            {"overrides": {"theta.theta": 5.0}, "score": 4.0},
        ]
        # The subgraph was re-executed with the winner and REPLACED:
        # downstream (rep) consumed the winner pass.
        assert result.outputs["theta"]["value"] == 3.0
        assert result.outputs["val"]["metrics"]["loss"] == 0.0
        report = read_json(result.run_dir, "artifacts", "rep", "report.json")
        assert report["value"] == 3.0 and report["loss"] == 0.0
        # The search record counts the trials; re-executed node records
        # reflect the final pass.
        record = read_json(result.run_dir, "nodes", "04-search.json")
        assert record["trials_executed"] == 3
        assert record["winner_reran"] == ["theta", "val"]
        theta_record = read_json(result.run_dir, "nodes", "02-theta.json")
        assert theta_record["outputs"]["value"] == 3.0
        # carry.json carries the winner pass (what $prev binds against).
        carry = read_json(result.run_dir, "carry.json")
        assert carry["theta"]["value"] == 3.0
        assert carry["search"]["best_params"] == {"theta.theta": 3.0}

    def test_two_identical_runs_are_identical(self, tmp_path, registry):
        a = run_document(
            parabola_document(tmp_path / "a"), asof=ASOF, registry=search_registry()
        )
        b = run_document(
            parabola_document(tmp_path / "b"), asof=ASOF, registry=search_registry()
        )
        assert a.run_hash == b.run_hash
        # Everything except rep (whose artifact PATH embeds the run root)
        # must be byte-identical, the whole trial ledger included.
        for key in ("src", "theta", "val", "search"):
            assert a.outputs[key] == b.outputs[key], key

    def test_dotted_paths_navigate_nested_params(self, tmp_path, registry):
        pipeline = parabola_pipeline(
            theta_params={"theta": 10.0, "opt": {"bias": 0.5}},
            search_params={"space": {"theta.opt.bias": [0.0, -7.0]}},
        )
        result = run_document(
            parabola_document(tmp_path, pipeline=pipeline),
            asof=ASOF,
            registry=registry,
        )
        assert result.state == "ran"
        # (10 + 0 - 3)^2 = 49 vs (10 - 7 - 3)^2 = 0 — the nested bias wins.
        assert result.outputs["search"]["best_params"] == {"theta.opt.bias": -7.0}
        assert result.outputs["theta"]["value"] == 3.0

    def test_override_into_missing_nested_param_fails_naming_the_trial(
        self, tmp_path, registry
    ):
        pipeline = parabola_pipeline(
            theta_params={"theta": 10.0, "opt": {"bias": 0.5}},
            search_params={"space": {"theta.opt.momentum": [0.9]}},
        )
        result = run_document(
            parabola_document(tmp_path, pipeline=pipeline),
            asof=ASOF,
            registry=registry,
        )
        assert result.state == "error"
        assert result.node_states["search"] == "error"
        assert "theta.opt.momentum" in result.error
        assert "never create them" in result.error
        assert "{'theta.opt.momentum': 0.9}" in result.error  # the trial named

    def test_seeds_ensemble_averages_and_winner_uses_document_seed(
        self, tmp_path, registry
    ):
        pipeline = parabola_pipeline(
            theta_params={"theta": 10.0, "seed": 0},
            search_params={"space": {"theta.theta": [0.0, 3.0]}, "seeds": [0, 2]},
        )
        result = run_document(
            parabola_document(tmp_path, pipeline=pipeline),
            asof=ASOF,
            registry=registry,
        )
        assert result.state == "ran"
        trials = result.outputs["search"]["trials"]
        # theta=0: seeds 0/2 -> values 0.00/0.02 -> losses 9, (0.02-3)^2.
        assert trials[0]["seed_scores"] == [
            pytest.approx(9.0),
            pytest.approx((0.02 - 3.0) ** 2),
        ]
        assert trials[0]["score"] == pytest.approx((9.0 + (0.02 - 3.0) ** 2) / 2)
        # theta=3 wins on the seed-mean; the seed override moved the value.
        assert trials[1]["seed_scores"][1] == pytest.approx((3.02 - 3.0) ** 2)
        assert result.outputs["search"]["best_params"] == {"theta.theta": 3.0}
        # 2 trials x 2 seeds = 4 subgraph re-executions.
        record = read_json(result.run_dir, "nodes", "04-search.json")
        assert record["trials_executed"] == 4
        # The final pass ran under the DOCUMENT's seed (0), not a trial's.
        assert result.outputs["theta"]["value"] == 3.0

    def test_n_trials_subsamples_the_grid(self, tmp_path, registry):
        pipeline = parabola_pipeline(
            search_params={
                "space": {"theta.theta": [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]},
                "n_trials": 3,
            }
        )
        result = run_document(
            parabola_document(tmp_path, pipeline=pipeline),
            asof=ASOF,
            registry=registry,
        )
        assert result.state == "ran"
        trials = result.outputs["search"]["trials"]
        assert len(trials) == 3
        record = read_json(result.run_dir, "nodes", "04-search.json")
        assert record["trials_executed"] == 3
        # The winner is the best of the EVALUATED subset, and the final
        # pass reflects it.
        best = min(trials, key=lambda t: t["score"])
        assert result.outputs["search"]["best_params"] == best["overrides"]
        theta = best["overrides"]["theta.theta"]
        assert result.outputs["theta"]["value"] == theta

    def test_sinks_reflect_the_final_pass(self, tmp_path, registry):
        doc = parabola_document(
            tmp_path,
            tracking=TrackingConfig(
                sinks=(SinkConfig(kind="dskit.pipeline.testing:MemoryTracker"),)
            ),
        )
        run_document(doc, asof=ASOF, registry=registry)
        sink = MemoryTracker.instances[-1]
        val_entries = [m for node, m in sink.metrics if node == "val"]
        assert val_entries[0]["metrics.loss"] == 49.0  # base pass (theta=10)
        assert val_entries[-1]["metrics.loss"] == 0.0  # winner re-log
        theta_entries = [m for node, m in sink.metrics if node == "theta"]
        assert theta_entries[-1]["value"] == 3.0

    def test_logged_params_are_this_document_s_declared_ones_not_the_winner_s(
        self, tmp_path, registry
    ):
        # Round-4 ruling (findings 1+2+3 and 5): ONE payload, at run
        # start, keys AND values following THIS execution's declared
        # document — every run of one document logs one config, and a
        # winner promoted by rerunning it as its own document carries the
        # override in THAT run's payload. The winner found here lives
        # where it happened: in the search node's outputs and record.
        doc = parabola_document(
            tmp_path,
            tracking=TrackingConfig(
                sinks=(SinkConfig(kind="dskit.pipeline.testing:MemoryTracker"),)
            ),
        )
        result = run_document(doc, asof=ASOF, registry=registry)
        logged = MemoryTracker.instances[-1].logged_params
        declared = doc.pipeline["theta"].params["theta"]
        winner = result.outputs["search"]["best_params"]["theta.theta"]
        assert winner != declared  # else vacuous
        assert logged["theta.theta"] == declared
        assert logged["src.x"] == doc.pipeline["src"].params["x"]  # untouched node

    def test_a_search_node_logs_its_own_declaration_not_a_resolved_score(
        self, tmp_path, registry
    ):
        # 'objective' is a $-ref the SEAM reads raw. Materializing it yields
        # the BASE pass's loss — an intermediate no pass ever selected on,
        # which would contradict, in one payload, both the val metrics
        # logged beside it and the theta it says the run used.
        doc = parabola_document(
            tmp_path,
            tracking=TrackingConfig(
                sinks=(SinkConfig(kind="dskit.pipeline.testing:MemoryTracker"),)
            ),
        )
        result = run_document(doc, asof=ASOF, registry=registry)
        sink = MemoryTracker.instances[-1]
        logged = sink.logged_params
        declared = doc.pipeline["search"].params
        assert logged["search.objective"] == declared["objective"]
        assert logged["search.space"] == declared["space"]
        assert logged["search.select"] == declared["select"]
        # The scores live in the metrics stream, where they mean something.
        losses = [m["metrics.loss"] for node, m in sink.metrics if node == "val"]
        assert losses[0] == 49.0  # the value that used to be logged as a param
        assert result.outputs["search"]["best_score"] == pytest.approx(min(losses))


# ---------------------------------------------------------------------------
# Driver: the synthetic market — every trial equals a from-scratch run
# ---------------------------------------------------------------------------

MARKET_SPLITS = TimeSplitConfig(
    train_end_ms=1048 * DAY, val_end_ms=1096 * DAY, test_end_ms=1097 * DAY
)

LO_GRID = [0.35, 0.44, 0.48]


def market_pipeline(lo=0.35, with_search=False):
    pipeline = {
        "events": NodeSpec(
            uses="synth-events",
            params={"n_events": 96, "n_instruments": 1, "seed": 11},
        ),
        "labels": NodeSpec(uses="synth-labels", inputs={"events": "$events.events"}),
        "clip": NodeSpec(
            uses="synth-clip",
            inputs={"events": "$events.events"},
            params={"lo": lo, "hi": 0.97},
        ),
        "market": NodeSpec(uses="synth-market", inputs={"events": "$clip.events"}),
        "qhat": NodeSpec(
            uses="synth-train",
            mode="train",
            inputs={"events": "$clip.events"},
            params={"min_train": 1},
        ),
        "validate": NodeSpec(
            uses="synth-score",
            inputs={
                "events": "$clip.events",
                "signal": "$qhat.signal",
                "baseline": "$market.signal",
                "outcomes": "$labels.outcomes",
            },
            params={"split": "val", "min_events": 1},
        ),
    }
    if with_search:
        pipeline["search"] = NodeSpec(
            uses="hpo-grid",
            params={
                "space": {"clip.lo": list(LO_GRID)},
                "objective": "$validate.metrics.loss",
                "select": "min",
            },
        )
        pipeline["report"] = NodeSpec(
            uses="synth-report",
            inputs={
                "best_score": "$search.best_score",
                "loss": "$validate.metrics.loss",
            },
        )
    return pipeline


def market_document(tmp_path, **kwargs):
    return PipelineDocument(
        name="mkt",
        pipeline=market_pipeline(**kwargs),
        splits=MARKET_SPLITS,
        outputs=OutputsConfig(run_root=str(tmp_path)),
    )


class TestEndToEndMarket:
    def test_each_trial_equals_a_from_scratch_pinned_run(self, tmp_path, registry):
        # Ground truth: one full pinned run per grid value.
        pinned = {}
        for lo in LO_GRID:
            result = run_document(
                market_document(tmp_path / "pinned", lo=lo),
                asof=ASOF,
                registry=registry,
            )
            assert result.state == "ran"
            pinned[lo] = result.outputs["validate"]["metrics"]["loss"]
        assert len(set(pinned.values())) == 3  # the grid genuinely discriminates
        best_lo = min(pinned, key=pinned.get)

        result = run_document(
            market_document(tmp_path / "searched", with_search=True),
            asof=ASOF,
            registry=registry,
        )
        assert result.state == "ran"
        search = result.outputs["search"]
        # Every trial's score IS the pinned run's loss — subgraph
        # re-execution against the scratch outputs equals a full run.
        for trial in search["trials"]:
            lo = trial["overrides"]["clip.lo"]
            assert trial["score"] == pytest.approx(pinned[lo])
        assert search["best_params"] == {"clip.lo": best_lo}
        assert search["best_score"] == pytest.approx(pinned[best_lo])
        # Downstream consumed the winner pass.
        assert result.outputs["validate"]["metrics"]["loss"] == pytest.approx(
            pinned[best_lo]
        )
        report = read_json(result.run_dir, "artifacts", "report", "report.json")
        assert report["loss"] == pytest.approx(pinned[best_lo])
        record = read_json(result.run_dir, "nodes", "07-search.json")
        assert record["trials_executed"] == 3
        assert record["winner_reran"] == ["clip", "market", "qhat", "validate"]
        # events/labels stayed clean — never re-executed.
        assert "events" not in record["winner_reran"]


# ---------------------------------------------------------------------------
# Driver: the seam's contract, probed by custom search nodes
# ---------------------------------------------------------------------------

PROBE = "tests.pipeline.test_kinds_search:ProbeSearch"


class TestSeamContract:
    def probe_doc(self, tmp_path, probe_params):
        params = {
            "space": {"theta.theta": [0.0]},
            "objective": "$val.metrics.loss",
        }
        params.update(probe_params)
        return parabola_document(
            tmp_path,
            pipeline=parabola_pipeline(search_params=params, search_uses=PROBE),
        )

    def test_trials_run_on_a_scratch_copy_of_the_base_pass(self, tmp_path, registry):
        # ProbeSearch evaluates theta=0 but selects NO winner: the base
        # outputs must survive untouched — trials never leak.
        result = run_document(
            self.probe_doc(tmp_path, {}), asof=ASOF, registry=registry
        )
        assert result.state == "ran"
        assert result.outputs["search"]["best_score"] == 9.0  # (0 - 3)^2, the trial
        assert result.outputs["theta"]["value"] == 10.0  # base pass intact
        assert result.outputs["val"]["metrics"]["loss"] == 49.0
        record = read_json(result.run_dir, "nodes", "04-search.json")
        assert record["trials_executed"] == 1
        assert "winner_reran" not in record  # empty best_params: no final pass

    def test_any_search_kinds_nonempty_best_params_is_applied(self, tmp_path, registry):
        # The winner pass is DRIVER semantics, not an hpo-grid feature.
        result = run_document(
            self.probe_doc(tmp_path, {"winner": {"theta.theta": 5.0}}),
            asof=ASOF,
            registry=registry,
        )
        assert result.state == "ran"
        assert result.outputs["theta"]["value"] == 5.0
        assert result.outputs["val"]["metrics"]["loss"] == 4.0
        record = read_json(result.run_dir, "nodes", "04-search.json")
        assert record["winner_reran"] == ["theta", "val"]

    def test_synth_search_placeholder_still_runs_as_a_no_op(self, tmp_path, registry):
        pipeline = parabola_pipeline(
            search_uses="dskit.pipeline.synthetic_nodes:SynthSearch"
        )
        result = run_document(
            parabola_document(tmp_path, pipeline=pipeline),
            asof=ASOF,
            registry=registry,
        )
        assert result.state == "ran"
        assert result.outputs["theta"]["value"] == 10.0  # nothing re-executed
        record = read_json(result.run_dir, "nodes", "04-search.json")
        assert record["trials_executed"] == 0

    def test_malformed_override_target_is_a_loud_trial_error(self, tmp_path, registry):
        result = run_document(
            self.probe_doc(tmp_path, {"probe": {"nope": 1}}),
            asof=ASOF,
            registry=registry,
        )
        assert result.state == "error"
        assert "'<node>.<param.path>'" in result.error
        assert "{'nope': 1}" in result.error

    def test_non_dict_overrides_are_a_loud_trial_error(self, tmp_path, registry):
        result = run_document(
            self.probe_doc(tmp_path, {"probe": "boom"}),
            asof=ASOF,
            registry=registry,
        )
        assert result.state == "error"
        assert "overrides must be a dict" in result.error

    def test_non_numeric_objective_value_is_a_loud_trial_error(
        self, tmp_path, registry
    ):
        # "$val.metrics" digs out the whole metrics DICT — the seam must
        # refuse it as the objective, naming the trial.
        pipeline = parabola_pipeline(search_params={"objective": "$val.metrics"})
        result = run_document(
            parabola_document(tmp_path, pipeline=pipeline),
            asof=ASOF,
            registry=registry,
        )
        assert result.state == "error"
        assert "objective must be numeric" in result.error

    def test_trial_node_failure_names_the_trial(self, tmp_path, registry):
        pipeline = parabola_pipeline(
            search_params={"space": {"theta.theta": [0.0, "boom"]}}
        )
        result = run_document(
            parabola_document(tmp_path, pipeline=pipeline),
            asof=ASOF,
            registry=registry,
        )
        assert result.state == "error"
        assert result.node_states["search"] == "error"
        assert "{'theta.theta': 'boom'}" in result.error

    def test_trial_validate_inputs_problems_fail_the_trial(self, tmp_path, registry):
        # Overriding a mid-graph transform re-executes it inside the
        # trial; theta's validate_inputs then rejects the poisoned
        # upstream value. (The data node itself is no longer a legal
        # space head — fingerprinted identity.)
        pipeline = parabola_pipeline(
            search_params={"space": {"relay.emit": ["poison"]}}
        )
        pipeline["relay"] = NodeSpec(
            uses="tests.pipeline.test_kinds_search:PassThrough",
            inputs={"x": "$src.x"},
            params={"emit": None},
        )
        pipeline["theta"] = NodeSpec(
            uses=THETA, inputs={"x": "$relay.x"}, params={"theta": 10.0}
        )
        result = run_document(
            parabola_document(tmp_path, pipeline=pipeline),
            asof=ASOF,
            registry=registry,
        )
        assert result.state == "error"
        assert "x must not be 'poison'" in result.error
        assert "{'relay.emit': 'poison'}" in result.error

    def test_runtime_overrides_may_not_address_identity_roles(self, tmp_path, registry):
        # The planner refuses unsearchable heads in the DOCUMENT's space;
        # a custom search kind handing ctx.rerun a data-node override
        # must hit the same wall at runtime.
        pipeline = parabola_pipeline(
            search_uses="tests.pipeline.test_kinds_search:ProbeSearch",
            search_params={
                "space": {"theta.theta": [0.0, 3.0]},
                "probe": {"src.x": "poison"},
            },
        )
        result = run_document(
            parabola_document(tmp_path, pipeline=pipeline),
            asof=ASOF,
            registry=registry,
        )
        assert result.state == "error"
        assert "may never re-tune" in result.error
        assert "'src.x'" in result.error

    def test_trial_output_contract_violations_fail_the_trial(self, tmp_path, registry):
        pipeline = parabola_pipeline(
            theta_params={"theta": 10.0, "misbehave": False},
            search_params={"space": {"theta.misbehave": [True]}},
        )
        result = run_document(
            parabola_document(tmp_path, pipeline=pipeline),
            asof=ASOF,
            registry=registry,
        )
        assert result.state == "error"
        assert "declared contract" in result.error

    def test_winner_pass_flipping_a_gate_verdict_refuses(self, tmp_path, registry):
        # Base theta=5 keeps the gate GO; the winner (theta=1, loss 0)
        # would flip it NO-GO AFTER halt decisions were made — refuse.
        pipeline = {
            "theta": NodeSpec(uses=THETA, params={"theta": 5.0}),
            "gatekeeper": NodeSpec(
                uses="tests.pipeline.test_kinds_search:CountGate",
                inputs={"value": "$theta.value"},
                params={"bar": 3.0},
            ),
            "val": NodeSpec(
                uses="tests.pipeline.test_kinds_search:ParabolaScore",
                inputs={"value": "$gatekeeper.n"},
                params={"split": "val", "center": 1.0},
            ),
            "search": NodeSpec(
                uses="hpo-grid",
                params={
                    "space": {"theta.theta": [5.0, 1.0]},
                    "objective": "$val.metrics.loss",
                },
            ),
        }
        result = run_document(
            parabola_document(tmp_path, pipeline=pipeline),
            asof=ASOF,
            registry=registry,
        )
        assert result.state == "error"
        assert result.node_states["search"] == "error"
        assert "flipped" in result.error and "NO-GO" in result.error


# ---------------------------------------------------------------------------
# The no-search path is byte-for-byte the old driver
# ---------------------------------------------------------------------------


class TestNoSearchUntouched:
    def test_no_search_run_artifacts_carry_no_search_fields(self, tmp_path):
        from tests.pipeline.dochelpers import banking_document, make_registry

        doc = banking_document(outputs=OutputsConfig(run_root=str(tmp_path)))
        result = run_document(doc, asof=ASOF, registry=make_registry())
        assert result.state == "ran"
        assert result.outputs["size"]["final_bankroll"] == pytest.approx(1020.0)
        nodes_dir = os.path.join(result.run_dir, "nodes")
        for name in sorted(os.listdir(nodes_dir)):
            record = read_json(nodes_dir, name)
            assert set(record) == {
                "node",
                "uses",
                "role",
                "status",
                "seconds",
                "outputs",
            }, name

    def test_non_search_nodes_never_see_the_seam(self, tmp_path, registry):
        pipeline = {
            "src": NodeSpec(
                uses="tests.pipeline.test_kinds_search:ConstSource", params={"x": 1}
            ),
            "probe": NodeSpec(
                uses="tests.pipeline.test_kinds_search:CtxProbeNode",
                inputs={"x": "$src.x"},
            ),
        }
        doc = parabola_document(tmp_path, pipeline=pipeline)
        CtxProbeNode.seen.clear()
        result = run_document(doc, asof=ASOF, registry=registry)
        assert result.state == "ran"
        assert CtxProbeNode.seen == [None]


class CtxProbeNode(Node):
    """Records the ctx.rerun each run sees — must be None off-search."""

    role = "transform"
    outputs = ("ok",)
    seen = []

    def run(self, ctx, inputs):
        CtxProbeNode.seen.append(ctx.rerun)
        return {"ok": True}
