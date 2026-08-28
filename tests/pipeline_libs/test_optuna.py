"""``optuna-search`` — the TPE-sampled variant of the docs/24 §8 search
contract (docs/25 §2 row 5), held to ``hpo-grid``'s bar.

Four layers, mirroring ``tests/pipeline/test_kinds_search.py``:

* unit — param validation (both space forms, strict continuous specs),
  run mechanics through the FakeRerun stub (convergence toward a known
  optimum, tie-breaking, direction, int/log/categorical suggestion
  domains), determinism (same seed = same trial sequence), registration;
* planner — the spec §8 rules asserted THROUGH ``plan()`` (bad
  objective/space/stale consumers refused), including the continuous
  form: the planner owns the STRUCTURAL space rules and leaves the
  range-spec grammar to this kind, so a spec-dict document plans — and
  so does a MALFORMED one, because a search node's params always defer
  plan-time ``validate_params``; that refusal lands at node
  construction, mid-run;
* driver — end-to-end ``run_document`` with a PRIVATE registry on the
  parabola fixture (analytic argmin), for BOTH space forms, the winner
  re-applied by the driver so downstream consumes the tuned pass, plus
  run-level determinism and loud trial failures;
* conformance — the suite over the pack's NODE_KINDS with the module
  imported under blocked heavy libraries. ``run()`` needs the driver's
  ``ctx.rerun`` seam, so the probe is not runnable — search is not a
  must-run role, and the reason is written down.

Every pinned constant here (winning theta, sampled-value expectations)
is deterministic: the TPE sampler is seeded and suggestions are drawn
over sorted space keys, so these tests either always pass or always
fail — no flake surface.
"""

import json
import os

import pytest

from dskit.pipeline.base import ConfigError, OutputsConfig, TimeSplitConfig
from dskit.pipeline.document import NodeSpec, PipelineDocument, load_document
from dskit.pipeline.driver import run_document
from dskit.pipeline.libs.optuna import NODE_KINDS, OptunaSearch, register
from dskit.pipeline.node import Node, NodeContext, NodeKindRegistry
from dskit.pipeline.planner import plan
from dskit.pipeline.synthetic_nodes import SynthSearch

ASOF = "2026-01-01"

#: Trivially-valid splits for score nodes that never read ctx.splits.
FLAT_SPLITS = TimeSplitConfig(train_end_ms=1, val_end_ms=2, test_end_ms=3)


def _example(name):
    return os.path.join(
        os.path.dirname(__file__), "..", "..", "examples", "pipeline", name
    )


def _document_obj(path):
    """One shipped document as raw JSON, every ``notes`` field dropped."""

    def strip(obj):
        if isinstance(obj, dict):
            return {k: strip(v) for k, v in obj.items() if k != "notes"}
        if isinstance(obj, list):
            return [strip(v) for v in obj]
        return obj

    with open(path, encoding="utf-8") as handle:
        return strip(json.load(handle))


EXAMPLE = _example("optuna-search.json")

#: The range-spec twin of EXAMPLE: same seam, continuous space.
CONTINUOUS_EXAMPLE = _example("optuna-continuous.json")


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
    """One trainable knob (role ``train``): value = theta."""

    role = "train"
    outputs = ("value",)

    def run(self, ctx, inputs):
        return {"value": self.params["theta"]}


class ParabolaScore(Node):
    """metrics.loss = (value - center)^2 (role ``score``): the val loss is
    minimized at value == center — the winner is unambiguous."""

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


HERE = "tests.pipeline_libs.test_optuna"


def parabola_pipeline(theta_params=None, search_params=None, val_split="val"):
    base_search = {
        "space": {"theta.theta": [0.0, 3.0, 5.0]},
        "objective": "$val.metrics.loss",
        "n_trials": 8,
        "seed": 0,
    }
    base_search.update(search_params or {})
    return {
        "src": NodeSpec(uses=f"{HERE}:ConstSource", params={"x": 1}),
        "theta": NodeSpec(
            uses=f"{HERE}:ThetaTrain",
            inputs={"x": "$src.x"},
            params=dict(theta_params or {"theta": 10.0}),
        ),
        "val": NodeSpec(
            uses=f"{HERE}:ParabolaScore",
            inputs={"value": "$theta.value"},
            params={"split": val_split, "center": 3.0},
        ),
        "search": NodeSpec(uses="optuna-search", params=base_search),
        "rep": NodeSpec(
            uses="dskit.pipeline.synthetic_nodes:SynthReport",
            inputs={
                "best_score": "$search.best_score",
                "value": "$theta.value",
                "loss": "$val.metrics.loss",
            },
        ),
    }


def parabola_document(tmp_path, pipeline=None, **overrides):
    base = {
        "name": "optuna_parabola",
        "pipeline": pipeline or parabola_pipeline(),
        "splits": FLAT_SPLITS,
        "outputs": OutputsConfig(run_root=str(tmp_path)),
    }
    base.update(overrides)
    return PipelineDocument(**base)


@pytest.fixture
def registry():
    """The PRIVATE registry (never DEFAULT_NODE_KINDS in tests): the
    pack's own :func:`register` is the only registration."""
    reg = NodeKindRegistry()
    register(reg)
    return reg


def read_json(run_dir, *parts):
    with open(os.path.join(run_dir, *parts), encoding="utf-8") as fh:
        return json.load(fh)


class FakeRerun:
    """A hand-rolled seam for unit tests (the test_kinds_search pattern):
    records call order and returns ``fn(overrides)``."""

    def __init__(self, fn):
        self.fn = fn
        self.calls = []

    def __call__(self, overrides):
        self.calls.append(dict(overrides))
        return self.fn(overrides)


def seam_ctx(tmp_path, rerun):
    return NodeContext(name="t", asof=ASOF, run_dir=str(tmp_path), rerun=rerun)


def search_node(**params):
    base = {
        "space": {"m.x": {"low": 0.0, "high": 10.0}},
        "objective": "$val.metrics.loss",
        "n_trials": 8,
        "seed": 0,
    }
    base.update(params)
    return OptunaSearch("search", base)


# ---------------------------------------------------------------------------
# Unit: validate_params
# ---------------------------------------------------------------------------


class TestValidateParams:
    def good(self, **over):
        params = {
            "space": {
                "train.lr": {"low": 1e-4, "high": 1e-1, "log": True},
                "train.units": [32, 64, 128],
            },
            "objective": "$validate.metrics.loss",
            "n_trials": 20,
            "seed": 0,
        }
        params.update(over)
        return params

    def test_both_space_forms_pass_together(self):
        assert OptunaSearch.validate_params(self.good(direction="maximize")) == []

    def test_materialized_objective_is_tolerated_at_construction(self):
        # The driver materializes the $-reference into the base pass's
        # float before constructing the node — construction must not
        # refuse it (the hpo-grid reading, shared).
        assert OptunaSearch.validate_params(self.good(objective=49.0)) == []
        OptunaSearch("search", self.good(objective=49.0))

    def test_unknown_knobs_refused_by_name_including_hpo_grid_only_ones(self):
        # `select` and `seeds` are hpo-grid's knobs, not this pack's —
        # accepting them silently would lie about the search semantics.
        for knob in ("select", "seeds", "pruner"):
            problems = OptunaSearch.validate_params(self.good(**{knob: 1}))
            assert any(knob in p for p in problems), (knob, problems)

    @pytest.mark.parametrize("bad", [None, {}, [], "x", 5])
    def test_space_must_be_a_non_empty_dict(self, bad):
        problems = OptunaSearch.validate_params(self.good(space=bad))
        assert any("space must be a non-empty dict" in p for p in problems)

    @pytest.mark.parametrize("key", ["train", "Train.lr", "9x.lr", "", 7])
    def test_space_keys_must_be_node_dot_param(self, key):
        problems = OptunaSearch.validate_params(self.good(space={key: [1]}))
        assert any("must be '<node>.<param.path>'" in p for p in problems)

    @pytest.mark.parametrize("values", [[], (), 5, "abc", None, True])
    def test_categorical_values_must_be_non_empty_lists(self, values):
        problems = OptunaSearch.validate_params(self.good(space={"t.lr": values}))
        assert any("non-empty list" in p for p in problems)

    @pytest.mark.parametrize("scalar", [[{"a": 1}], [[1]], [float("inf")]])
    def test_categorical_values_must_be_json_scalars(self, scalar):
        problems = OptunaSearch.validate_params(self.good(space={"t.lr": scalar}))
        assert any("JSON scalars" in p for p in problems)

    def test_spec_dict_unknown_keys_refused_by_name(self):
        spec = {"low": 0.0, "high": 1.0, "step": 0.1}
        problems = OptunaSearch.validate_params(self.good(space={"t.lr": spec}))
        assert any("'step'" in p and "unknown spec key" in p for p in problems)

    def test_spec_dict_non_string_keys_refused(self):
        problems = OptunaSearch.validate_params(
            self.good(space={"t.lr": {1: 2, "low": 0.0, "high": 1.0}})
        )
        assert any("spec keys must be strings" in p for p in problems)

    @pytest.mark.parametrize(
        "spec",
        [
            {},
            {"low": 0.0},
            {"high": 1.0},
            {"low": "0", "high": 1.0},
            {"low": None, "high": 1.0},
            {"low": True, "high": 1.0},
            {"low": float("nan"), "high": 1.0},
            {"low": 0.0, "high": float("inf")},
        ],
    )
    def test_spec_dict_low_high_required_finite_numbers(self, spec):
        problems = OptunaSearch.validate_params(self.good(space={"t.lr": spec}))
        assert any("must be a finite number" in p for p in problems)

    @pytest.mark.parametrize("spec", [{"low": 1.0, "high": 1.0}, {"low": 2, "high": 1}])
    def test_spec_dict_low_must_be_below_high(self, spec):
        problems = OptunaSearch.validate_params(self.good(space={"t.lr": spec}))
        assert any("low must be < high" in p for p in problems)

    @pytest.mark.parametrize("flag", ["log", "int"])
    @pytest.mark.parametrize("bad", [1, "yes", None, [], {}])
    def test_spec_dict_flags_must_be_strict_bools(self, flag, bad):
        spec = {"low": 1.0, "high": 2.0, flag: bad}
        problems = OptunaSearch.validate_params(self.good(space={"t.lr": spec}))
        assert any("must be a bool" in p for p in problems)

    def test_log_spec_needs_positive_low(self):
        spec = {"low": 0.0, "high": 1.0, "log": True}
        problems = OptunaSearch.validate_params(self.good(space={"t.lr": spec}))
        assert any("log specs need low > 0" in p for p in problems)

    def test_int_spec_needs_integer_bounds(self):
        spec = {"low": 1.5, "high": 8, "int": True}
        problems = OptunaSearch.validate_params(self.good(space={"t.k": spec}))
        assert any("integer bounds" in p for p in problems)

    def test_log_int_spec_needs_low_at_least_one(self):
        spec = {"low": 0, "high": 8, "int": True, "log": True}
        problems = OptunaSearch.validate_params(self.good(space={"t.k": spec}))
        assert any("log int specs need low >= 1" in p for p in problems)
        ok = {"low": 1, "high": 8, "int": True, "log": True}
        assert OptunaSearch.validate_params(self.good(space={"t.k": ok})) == []

    def test_objective_required_and_ref_shaped(self):
        params = self.good()
        del params["objective"]
        assert any(
            "objective is required" in p for p in OptunaSearch.validate_params(params)
        )
        problems = OptunaSearch.validate_params(self.good(objective="loss"))
        assert any("'$node.path' reference" in p for p in problems)

    @pytest.mark.parametrize("bad", [0, -1, True, 1.5, "3", None, []])
    def test_n_trials_required_positive_int(self, bad):
        problems = OptunaSearch.validate_params(self.good(n_trials=bad))
        assert any("n_trials" in p for p in problems)

    def test_n_trials_missing_is_refused(self):
        params = self.good()
        del params["n_trials"]
        assert any("n_trials" in p for p in OptunaSearch.validate_params(params))

    @pytest.mark.parametrize("bad", [-1, True, "0", 1.0, None])
    def test_seed_required_non_negative_int(self, bad):
        problems = OptunaSearch.validate_params(self.good(seed=bad))
        assert any("seed" in p for p in problems)

    def test_seed_missing_is_refused(self):
        params = self.good()
        del params["seed"]
        assert any("seed" in p for p in OptunaSearch.validate_params(params))

    @pytest.mark.parametrize("bad", ["up", "min", "max", 1, None, []])
    def test_direction_membership(self, bad):
        problems = OptunaSearch.validate_params(self.good(direction=bad))
        assert any("direction" in p for p in problems)

    @pytest.mark.parametrize("good_dir", ["minimize", "maximize"])
    def test_direction_legal_values(self, good_dir):
        assert OptunaSearch.validate_params(self.good(direction=good_dir)) == []


# ---------------------------------------------------------------------------
# Unit: run mechanics through the FakeRerun stub
# ---------------------------------------------------------------------------


class TestRunMechanics:
    def test_without_the_driver_seam_run_refuses(self, tmp_path):
        ctx = NodeContext(name="t", asof=ASOF, run_dir=str(tmp_path))
        with pytest.raises(RuntimeError, match="optuna-search runs under the driver"):
            search_node().run(ctx, {})

    def test_tpe_converges_toward_the_known_optimum(self, tmp_path):
        # Continuous space, analytic argmin at x = 3: after 40 seeded
        # trials the best must land inside the unit bowl around it.
        fake = FakeRerun(lambda o: (o["m.x"] - 3.0) ** 2)
        out = search_node(n_trials=40, seed=7).run(seam_ctx(tmp_path, fake), {})
        assert len(out["trials"]) == 40 and len(fake.calls) == 40
        assert all(0.0 <= c["m.x"] <= 10.0 for c in fake.calls)
        assert out["best_score"] < 1.0
        assert abs(out["best_params"]["m.x"] - 3.0) < 1.0
        # The ledger and the winner agree: best IS the min over trials.
        assert out["best_score"] == min(t["score"] for t in out["trials"])

    def test_categorical_space_samples_only_grid_values(self, tmp_path):
        fake = FakeRerun(lambda o: abs(o["a.x"] - 3.0))
        out = search_node(space={"a.x": [0.0, 3.0, 5.0]}, n_trials=10).run(
            seam_ctx(tmp_path, fake), {}
        )
        assert all(c["a.x"] in (0.0, 3.0, 5.0) for c in fake.calls)
        assert out["best_params"] == {"a.x": 3.0}
        assert out["best_score"] == 0.0

    def test_ties_go_to_the_first_evaluated_trial(self, tmp_path):
        fake = FakeRerun(lambda o: 1.0)
        out = search_node(space={"a.x": [1, 2, 3]}, n_trials=6).run(
            seam_ctx(tmp_path, fake), {}
        )
        assert out["trials"][0]["overrides"] == fake.calls[0]
        assert out["best_params"] == fake.calls[0]
        assert out["best_score"] == 1.0

    def test_direction_maximize_picks_the_other_end(self, tmp_path):
        fake = FakeRerun(lambda o: o["m.x"])
        out = search_node(n_trials=20, direction="maximize").run(
            seam_ctx(tmp_path, fake), {}
        )
        assert out["best_score"] == max(t["score"] for t in out["trials"])
        assert (
            out["best_params"]["m.x"]
            == out["trials"][
                [t["score"] for t in out["trials"]].index(out["best_score"])
            ]["overrides"]["m.x"]
        )

    def test_int_spec_suggests_integers_in_range(self, tmp_path):
        fake = FakeRerun(lambda o: float(o["m.k"]))
        search_node(space={"m.k": {"low": 1, "high": 8, "int": True}}, n_trials=12).run(
            seam_ctx(tmp_path, fake), {}
        )
        for call in fake.calls:
            k = call["m.k"]
            assert isinstance(k, int) and not isinstance(k, bool)
            assert 1 <= k <= 8

    def test_log_spec_stays_inside_its_bounds(self, tmp_path):
        fake = FakeRerun(lambda o: o["m.lr"])
        spec = {"low": 1e-4, "high": 1e-1, "log": True}
        search_node(space={"m.lr": spec}, n_trials=12).run(seam_ctx(tmp_path, fake), {})
        assert all(1e-4 <= c["m.lr"] <= 1e-1 for c in fake.calls)

    def test_mixed_space_suggests_every_key_each_trial(self, tmp_path):
        fake = FakeRerun(lambda o: o["b.y"] + o["a.x"])
        out = search_node(
            space={"a.x": [1, 2], "b.y": {"low": 0.0, "high": 1.0}}, n_trials=6
        ).run(seam_ctx(tmp_path, fake), {})
        for call in fake.calls:
            assert set(call) == {"a.x", "b.y"}
            assert call["a.x"] in (1, 2) and 0.0 <= call["b.y"] <= 1.0
        assert set(out["best_params"]) == {"a.x", "b.y"}

    def test_trials_ledger_mirrors_call_order(self, tmp_path):
        fake = FakeRerun(lambda o: o["m.x"])
        out = search_node(n_trials=5).run(seam_ctx(tmp_path, fake), {})
        assert [t["overrides"] for t in out["trials"]] == fake.calls

    def test_a_failing_trial_propagates_loudly(self, tmp_path):
        def boom(overrides):
            raise RuntimeError("trial exploded")

        with pytest.raises(RuntimeError, match="trial exploded"):
            search_node(n_trials=3).run(seam_ctx(tmp_path, FakeRerun(boom)), {})


class TestNonFiniteObjective:
    """S1 #3, the optuna side — VERIFIED, not assumed: optuna marks a
    NaN-returning trial FAIL and quietly carries on, but this node's own
    ``results`` ledger has already recorded the NaN row by then, and the
    strict-compare winner loop lets a first-trial NaN stick as "best"
    (``x < nan`` is always False); a ``-inf`` return optuna even accepts
    as COMPLETE and crowns itself. Non-finite must be refused BY NAME at
    the instant the trial reports — the shared ``hpo-grid`` rule."""

    def _nan_at(self, calls, at):
        def fn(overrides):
            calls.append(dict(overrides))
            return float("nan") if len(calls) == at else 1.0

        return fn

    def test_a_first_trial_nan_cannot_win(self, tmp_path):
        calls = []
        fake = FakeRerun(self._nan_at(calls, 1))
        with pytest.raises(ValueError) as excinfo:
            search_node(space={"a.x": [1, 2, 3]}, n_trials=6).run(
                seam_ctx(tmp_path, fake), {}
            )
        message = str(excinfo.value)
        assert "non-finite objective" in message
        assert "'a.x'" in message  # the trial is named by its overrides
        assert "nan" in message
        assert len(calls) == 1  # refused at the report, not after the study

    def test_a_mid_run_nan_is_refused_not_ranked(self, tmp_path):
        calls = []
        fake = FakeRerun(self._nan_at(calls, 3))
        with pytest.raises(ValueError, match="non-finite objective"):
            search_node(space={"a.x": [1, 2, 3]}, n_trials=6).run(
                seam_ctx(tmp_path, fake), {}
            )
        assert len(calls) == 3

    @pytest.mark.parametrize("bad", [float("inf"), float("-inf")])
    def test_infinities_are_refused_too(self, tmp_path, bad):
        # -inf is the sharp one: optuna itself accepts it as a COMPLETE
        # trial and it wins every minimize comparison outright.
        fake = FakeRerun(lambda o: bad)
        with pytest.raises(ValueError, match="non-finite objective"):
            search_node(space={"a.x": [1, 2, 3]}, n_trials=4).run(
                seam_ctx(tmp_path, fake), {}
            )


class TestDeterminism:
    def evaluate(self, tmp_path, seed, n_trials=15):
        fake = FakeRerun(lambda o: (o["m.x"] - 3.0) ** 2 + o["c.k"])
        node = search_node(
            space={"m.x": {"low": 0.0, "high": 10.0}, "c.k": [0, 1]},
            n_trials=n_trials,
            seed=seed,
        )
        out = node.run(seam_ctx(tmp_path, fake), {})
        return out, fake.calls

    def test_same_seed_same_trial_sequence_and_winner(self, tmp_path):
        out_a, calls_a = self.evaluate(tmp_path / "a", seed=11)
        out_b, calls_b = self.evaluate(tmp_path / "b", seed=11)
        assert calls_a == calls_b  # the whole suggestion sequence, in order
        assert out_a["trials"] == out_b["trials"]
        assert out_a["best_params"] == out_b["best_params"]
        assert out_a["best_score"] == out_b["best_score"]

    def test_a_different_seed_explores_differently(self, tmp_path):
        _out_a, calls_a = self.evaluate(tmp_path / "a", seed=11)
        _out_b, calls_b = self.evaluate(tmp_path / "b", seed=12)
        # Deterministic either way; distinct seeds draw distinct floats
        # from the very first suggestion.
        assert calls_a != calls_b


class TestRegister:
    def test_registers_unowned_and_skips_if_present(self):
        reg = NodeKindRegistry()
        register(reg)
        assert reg.get("optuna-search") == (OptunaSearch, False)
        register(reg)  # idempotent — no duplicate-name raise
        assert reg.get("optuna-search") == (OptunaSearch, False)

    def test_never_shadows_an_existing_claim(self):
        reg = NodeKindRegistry()
        reg.register("optuna-search", SynthSearch)
        register(reg)
        assert reg.get("optuna-search") == (SynthSearch, False)

    def test_node_kinds_table_is_the_registration_source(self):
        assert NODE_KINDS == (("optuna-search", OptunaSearch),)


# ---------------------------------------------------------------------------
# Planner: the spec §8 rules, asserted through plan()
# ---------------------------------------------------------------------------


class TestPlannerRules:
    def test_parabola_document_plans(self, tmp_path, registry):
        plan(parabola_document(tmp_path), registry)

    def test_literal_objective_refused_at_plan(self, tmp_path, registry):
        pipeline = parabola_pipeline(search_params={"objective": 5.0})
        with pytest.raises(ConfigError, match=r"must be a \$-reference"):
            plan(parabola_document(tmp_path, pipeline=pipeline), registry)

    def test_objective_must_come_from_a_score_node(self, tmp_path, registry):
        pipeline = parabola_pipeline(search_params={"objective": "$theta.value"})
        with pytest.raises(ConfigError, match="must come from a 'score' node"):
            plan(parabola_document(tmp_path, pipeline=pipeline), registry)

    def test_selection_consults_validation_only(self, tmp_path, registry):
        pipeline = parabola_pipeline(val_split="train")
        with pytest.raises(ConfigError, match="selection consults validation only"):
            plan(parabola_document(tmp_path, pipeline=pipeline), registry)

    def test_space_undeclared_node_refused(self, tmp_path, registry):
        pipeline = parabola_pipeline(search_params={"space": {"ghost.lr": [1]}})
        with pytest.raises(ConfigError, match="no node 'ghost'"):
            plan(parabola_document(tmp_path, pipeline=pipeline), registry)

    def test_space_must_address_existing_params(self, tmp_path, registry):
        pipeline = parabola_pipeline(search_params={"space": {"theta.ghost": [1]}})
        with pytest.raises(ConfigError, match="declares no param 'ghost'"):
            plan(parabola_document(tmp_path, pipeline=pipeline), registry)

    def test_stale_consumer_refuses_to_plan(self, tmp_path, registry):
        # 'leech' consumes the tuned node's output but is neither
        # re-executed with the winner nor downstream of the search.
        pipeline = parabola_pipeline()
        pipeline["leech"] = NodeSpec(
            uses="dskit.pipeline.synthetic_nodes:SynthReport",
            inputs={"value": "$theta.value"},
        )
        with pytest.raises(ConfigError, match=r"\['leech'\].*stale pre-winner"):
            plan(parabola_document(tmp_path, pipeline=pipeline), registry)

    def test_continuous_specs_now_plan(self, tmp_path, registry):
        # Flipped DELIBERATELY at integration (the round-1 pinned gap):
        # the planner owns the STRUCTURAL space rules (keys address
        # declared params, winner-consistency) plus non-emptiness of
        # either value shape, and passes a NON-EMPTY range DICT through
        # untouched ({} refuses at plan — pinned in
        # tests/pipeline/test_kinds_search.py). The RANGE grammar is
        # this kind's and it does NOT run at plan — see the next test
        # for where it bites.
        search_params = {"space": {"theta.theta": {"low": 0.0, "high": 5.0}}}
        node_params = {
            "space": search_params["space"],
            "objective": "$val.metrics.loss",
            "n_trials": 4,
            "seed": 0,
        }
        assert OptunaSearch.validate_params(node_params) == []
        pipeline = parabola_pipeline(search_params=search_params)
        the_plan = plan(parabola_document(tmp_path, pipeline=pipeline), registry)
        assert "search" in the_plan.order

    def test_a_malformed_range_spec_refuses_at_construction_not_at_plan(
        self, tmp_path, registry
    ):
        # WHERE the range grammar bites, pinned rather than asserted in
        # prose. A search node's params always carry a $-ref objective, so
        # plan-time validate_params DEFERS (planner._has_unresolved_ref) —
        # for hpo-grid's space values exactly as much as for these range
        # specs. So an INVERTED range plans clean: the kind's validator
        # refuses at NODE CONSTRUCTION, which during a run lands after the
        # upstream nodes have executed and the run dir exists.
        bad_space = {"theta.theta": {"low": 5.0, "high": 0.0}}
        bad = {
            "space": bad_space,
            "objective": "$val.metrics.loss",
            "n_trials": 4,
            "seed": 0,
        }
        pipeline = parabola_pipeline(search_params={"space": bad_space})
        the_plan = plan(parabola_document(tmp_path, pipeline=pipeline), registry)
        assert "search" in the_plan.deferred_params
        problems = OptunaSearch.validate_params(bad)
        assert any("low" in p for p in problems)
        with pytest.raises(ConfigError, match="low"):
            OptunaSearch("search", bad)


# ---------------------------------------------------------------------------
# Driver: end to end on the parabola (analytic winner)
# ---------------------------------------------------------------------------


class TestEndToEndParabola:
    def test_winner_semantics_records_and_artifacts(self, tmp_path, registry):
        result = run_document(parabola_document(tmp_path), asof=ASOF, registry=registry)
        assert result.state == "ran"
        search = result.outputs["search"]
        # 8 seeded TPE trials over {0, 3, 5}: the analytic argmin of
        # (theta - 3)^2 is sampled (pinned seed) and must win.
        assert len(search["trials"]) == 8
        assert search["best_params"] == {"theta.theta": 3.0}
        assert search["best_score"] == 0.0
        sampled = {t["overrides"]["theta.theta"] for t in search["trials"]}
        assert sampled <= {0.0, 3.0, 5.0} and 3.0 in sampled
        # The subgraph was re-executed with the winner and REPLACED:
        # downstream (rep) consumed the winner pass.
        assert result.outputs["theta"]["value"] == 3.0
        assert result.outputs["val"]["metrics"]["loss"] == 0.0
        report = read_json(result.run_dir, "artifacts", "rep", "report.json")
        assert report["value"] == 3.0 and report["loss"] == 0.0
        record = read_json(result.run_dir, "nodes", "04-search.json")
        assert record["trials_executed"] == 8
        assert record["winner_reran"] == ["theta", "val"]
        # carry.json carries the winner pass (what $prev binds against).
        carry = read_json(result.run_dir, "carry.json")
        assert carry["theta"]["value"] == 3.0
        assert carry["search"]["best_params"] == {"theta.theta": 3.0}

    def test_two_identical_runs_are_identical(self, tmp_path):
        reg_a, reg_b = NodeKindRegistry(), NodeKindRegistry()
        register(reg_a)
        register(reg_b)
        a = run_document(parabola_document(tmp_path / "a"), asof=ASOF, registry=reg_a)
        b = run_document(parabola_document(tmp_path / "b"), asof=ASOF, registry=reg_b)
        assert a.run_hash == b.run_hash
        # Everything except rep (whose artifact PATH embeds the run root)
        # must be identical, the whole trial ledger included.
        for key in ("src", "theta", "val", "search"):
            assert a.outputs[key] == b.outputs[key], key

    def test_a_continuous_space_runs_end_to_end(self, tmp_path, registry):
        # The whole point of the range-spec form: a continuous document
        # PLANS (the planner owns the structural space rules only) and
        # RUNS — the TPE sampler draws real-valued theta off any grid,
        # and the driver re-applies the winner so downstream consumes the
        # tuned pass exactly as it does for the categorical form.
        pipeline = parabola_pipeline(
            search_params={
                "space": {"theta.theta": {"low": 0.0, "high": 6.0}},
                "n_trials": 25,
                "seed": 3,
            }
        )
        result = run_document(
            parabola_document(tmp_path, pipeline=pipeline),
            asof=ASOF,
            registry=registry,
        )
        assert result.state == "ran"
        search = result.outputs["search"]
        assert len(search["trials"]) == 25
        theta = search["best_params"]["theta.theta"]
        # Off-grid float inside the declared interval, converged onto the
        # analytic argmin 3.0 (seeded: this is a fact, not a hope).
        assert 0.0 < theta < 6.0
        assert theta != int(theta)
        assert abs(theta - 3.0) < 0.5
        assert search["best_score"] == min(t["score"] for t in search["trials"])
        # The winner pass is what downstream consumed.
        assert result.outputs["theta"]["value"] == theta
        assert result.outputs["val"]["metrics"]["loss"] == search["best_score"]
        record = read_json(result.run_dir, "nodes", "04-search.json")
        assert record["trials_executed"] == 25
        assert record["winner_reran"] == ["theta", "val"]

    def test_trial_node_failure_names_the_trial(self, tmp_path, registry):
        pipeline = parabola_pipeline(
            search_params={"space": {"theta.theta": ["boom"]}, "n_trials": 1}
        )
        result = run_document(
            parabola_document(tmp_path, pipeline=pipeline),
            asof=ASOF,
            registry=registry,
        )
        assert result.state == "error"
        assert result.node_states["search"] == "error"
        assert "{'theta.theta': 'boom'}" in result.error


# ---------------------------------------------------------------------------
# The example documents (both space forms): load, hash, PLAN and RUN for real
# ---------------------------------------------------------------------------


class TestExampleDocument:
    def test_loads_and_hashes_stably(self):
        doc = load_document(EXAMPLE)
        assert doc.name == "optuna-search-demo"
        assert load_document(EXAMPLE).hash == doc.hash

    def test_plans_via_the_real_planner(self):
        # Every `uses` is an import reference, so the default registry
        # needs no registration for this document to plan.
        the_plan = plan(load_document(EXAMPLE))
        assert the_plan.role_of("search") == "search"
        assert ("search", "report") in the_plan.edges

    def test_runs_end_to_end_via_the_real_driver(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)  # outputs.run_root "" -> ./pipeline_runs
        result = run_document(load_document(EXAMPLE), asof=ASOF)
        assert result.state == "ran"
        search = result.outputs["search"]
        assert len(search["trials"]) == 6
        assert search["best_params"]["clip.lo"] in (0.35, 0.44, 0.48)
        # The winner pass is what downstream consumed.
        assert result.outputs["validate"]["metrics"]["loss"] == pytest.approx(
            search["best_score"]
        )
        record = read_json(result.run_dir, "nodes", "07-search.json")
        assert record["trials_executed"] == 6
        assert "clip" in record["winner_reran"]


class TestContinuousExampleDocument:
    """``optuna-continuous.json`` — the categorical example's twin, and
    the shipped proof that the range-spec form survives the whole path:
    JSON on disk, the real planner, a completed run."""

    def test_the_document_ships_the_range_spec_form(self):
        # The example EARNS its place by being continuous; if a later
        # edit turned this space back into a list, the run test below
        # would still pass and the example would silently duplicate
        # optuna-search.json.
        space = load_document(CONTINUOUS_EXAMPLE).pipeline["search"].params["space"]
        assert space == {"clip.lo": {"low": 0.3, "high": 0.55}}

    def test_the_twins_differ_only_in_name_and_the_space_shape(self):
        # BOTH documents' notes assert this relationship in prose; a claim
        # in two places with nothing pinning it is a scheduled bug, so
        # this is the pin. `notes` is stripped (it is excluded from
        # identity), so what is compared is what the two documents
        # COMPUTE — wiring, params, splits, budget. Only `name` (two run
        # series) and the space VALUE may differ; a later edit to either
        # document's pipeline now falsifies both notes loudly.
        categorical = _document_obj(EXAMPLE)
        continuous = _document_obj(CONTINUOUS_EXAMPLE)
        assert categorical.pop("name") != continuous.pop("name")
        grid = categorical["pipeline"]["search"]["params"].pop("space")
        spec = continuous["pipeline"]["search"]["params"].pop("space")
        assert isinstance(grid["clip.lo"], list)  # the categorical form
        assert isinstance(spec["clip.lo"], dict)  # the range form
        assert categorical == continuous

    def test_loads_and_hashes_stably(self):
        doc = load_document(CONTINUOUS_EXAMPLE)
        assert doc.name == "optuna-continuous-demo"
        assert load_document(CONTINUOUS_EXAMPLE).hash == doc.hash

    def test_plans_via_the_real_planner(self):
        # The gap this example exists to prove closed: a spec-dict space
        # reaches plan() and passes it, with no registration needed (every
        # `uses` is an import reference).
        the_plan = plan(load_document(CONTINUOUS_EXAMPLE))
        assert the_plan.role_of("search") == "search"
        assert ("search", "report") in the_plan.edges

    def test_runs_end_to_end_via_the_real_driver(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)  # outputs.run_root "" -> ./pipeline_runs
        result = run_document(load_document(CONTINUOUS_EXAMPLE), asof=ASOF)
        assert result.state == "ran"
        search = result.outputs["search"]
        assert len(search["trials"]) == 6
        lo = search["best_params"]["clip.lo"]
        assert 0.3 < lo < 0.55  # a sampled interior float, not a bound
        assert all(0.3 <= t["overrides"]["clip.lo"] <= 0.55 for t in search["trials"])
        # The winner pass is what downstream consumed.
        assert result.outputs["validate"]["metrics"]["loss"] == pytest.approx(
            search["best_score"]
        )
        record = read_json(result.run_dir, "nodes", "07-search.json")
        assert record["trials_executed"] == 6
        assert "clip" in record["winner_reran"]


# ---------------------------------------------------------------------------
# Conformance: the pack under the toolkit's own bar
# ---------------------------------------------------------------------------

from dskit.pipeline.conformance import NodeProbe, conformance_suite

#: run() is not exercisable standalone BY DESIGN: it refuses without the
#: driver-injected ``ctx.rerun`` seam (docs/24 §8), exactly like the
#: toolkit's own hpo-grid probe. Search is not a must-run role, so the
#: probe supplies everything else and writes the reason down.
NOT_RUNNABLE = (
    "run() needs the driver's ctx.rerun subgraph re-execution seam "
    "(docs/24 §8) — exercised end-to-end via run_document above"
)

TestConformance = conformance_suite(
    registry=NODE_KINDS,
    module="dskit.pipeline.libs.optuna",
    probes={
        "optuna-search": NodeProbe(
            # objective's VALUE is a $-reference string BY DESIGN — the
            # default-deny closes unknown KEYS, never reference values.
            # The space carries BOTH forms: categorical list + continuous
            # log spec (the pack's addition over hpo-grid).
            params={
                "space": {
                    "train.lr": {"low": 1e-4, "high": 1e-1, "log": True},
                    "train.units": [32, 64, 128],
                },
                "objective": "$validate.metrics.loss",
                "n_trials": 8,
                "seed": 0,
                "direction": "minimize",
            },
            required=("space", "objective", "n_trials", "seed"),
            not_runnable_reason=NOT_RUNNABLE,
        ),
    },
    expected_roles={"optuna-search": "search"},
)
