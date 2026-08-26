"""The planner: IMPORT + PLAN, role rules as DAG checks (docs/24 §5, §8, §9)."""

import pytest

from dskit.pipeline.base import ConfigError, TimeSplitConfig
from dskit.pipeline.document import NodeSpec, PipelineDocument, RandomSplitSpec
from dskit.pipeline.node import Node
from dskit.pipeline.planner import plan
from tests.pipeline.dochelpers import (
    BANKING_SPLITS,
    banking_document,
    banking_pipeline,
    make_registry,
)


@pytest.fixture
def registry():
    return make_registry()


def doc_of(pipeline, **overrides):
    base = {"name": "plan-test", "pipeline": pipeline}
    base.update(overrides)
    return PipelineDocument(**base)


class TestHappyPath:
    def test_banking_document_plans_in_dependency_order(self, registry):
        p = plan(banking_document(), registry)
        pos = {k: i for i, k in enumerate(p.order)}
        for src, dst in p.edges:
            assert pos[src] < pos[dst], f"{src} must precede {dst}"
        assert set(p.order) == set(banking_pipeline())

    def test_declaration_order_breaks_ties(self, registry):
        # labels/bank/clip all depend only on events; family only on bank.
        p = plan(banking_document(), registry)
        assert p.order[0] == "events"
        ready_after_events = [k for k in p.order if k in ("labels", "bank", "clip")]
        assert ready_after_events == ["labels", "bank", "clip"]

    def test_plan_is_deterministic(self, registry):
        a, b = plan(banking_document(), registry), plan(banking_document(), registry)
        assert a.order == b.order and a.edges == b.edges
        assert a.to_obj() == b.to_obj()

    def test_plan_to_obj_names_everything(self, registry):
        obj = plan(banking_document(), registry).to_obj()
        assert obj["document_hash"] == banking_document().hash
        assert obj["nodes"]["edge_test"]["role"] == "stat_test"
        assert obj["nodes"]["edge_test"]["owned"] is True
        assert obj["nodes"]["size"]["params_validation"] == "checked"

    def test_descendants_are_transitive(self, registry):
        p = plan(banking_document(), registry)
        downstream = p.descendants("edge_test")
        assert downstream == {"size", "report"}
        assert "report" in p.descendants("events")


class TestImportAndRoles:
    def test_unresolvable_uses_accumulate(self, registry):
        pipeline = {
            "a": NodeSpec(uses="no-such-kind"),
            "b": NodeSpec(uses="no.such.module:Thing"),
        }
        with pytest.raises(ConfigError) as exc:
            plan(doc_of(pipeline), registry)
        assert len(exc.value.errors) == 2

    def test_config_role_cross_checked_against_the_class(self, registry):
        ok = {"e": NodeSpec(uses="synth-events", role="data")}
        plan(doc_of(ok), registry)
        bad = {"e": NodeSpec(uses="synth-events", role="capital")}
        with pytest.raises(ConfigError, match="the label is the class's to give"):
            plan(doc_of(bad), registry)

    def test_stat_test_must_be_the_owned_kind(self, registry):
        pipeline = banking_pipeline()
        pipeline["edge_test"] = NodeSpec(
            uses="dskit.pipeline.synthetic_nodes:SynthStatTest",
            inputs={"scores": "$validate.cluster_scores"},
            params={"alpha": 0.05},
        )
        with pytest.raises(ConfigError, match="toolkit-owned"):
            plan(doc_of(pipeline, splits=TimeSplitConfig(**BANKING_SPLITS)), registry)

    def test_cycle_refused_naming_the_members(self, registry):
        pipeline = {
            "a": NodeSpec(uses="synth-clip", inputs={"events": "$b.events"}),
            "b": NodeSpec(uses="synth-clip", inputs={"events": "$a.events"}),
        }
        with pytest.raises(ConfigError, match="cycle"):
            plan(doc_of(pipeline), registry)


class TestWireContracts:
    def test_ref_into_undeclared_output_refused(self, registry):
        pipeline = {
            "events": NodeSpec(uses="synth-events"),
            "labels": NodeSpec(uses="synth-labels", inputs={"events": "$events.ghost"}),
        }
        with pytest.raises(ConfigError, match="no 'ghost'"):
            plan(doc_of(pipeline), registry)

    def test_param_refs_also_create_edges_and_checks(self, registry):
        pipeline = {
            "events": NodeSpec(uses="synth-events"),
            "rep": NodeSpec(uses="synth-report", params={"n": "$events.newest_ms"}),
        }
        p = plan(doc_of(pipeline), registry)
        assert ("events", "rep") in p.edges


class TestRoleRules:
    def test_data_nodes_take_no_inputs_or_node_params(self, registry):
        with_inputs = {
            "events": NodeSpec(uses="synth-events"),
            "more": NodeSpec(uses="synth-events", inputs={"x": "$events.events"}),
        }
        with pytest.raises(ConfigError, match="takes no inputs"):
            plan(doc_of(with_inputs), registry)
        with_params = {
            "events": NodeSpec(uses="synth-events"),
            "more": NodeSpec(uses="synth-events", params={"seed": "$events.newest_ms"}),
        }
        with pytest.raises(ConfigError, match="fully literal"):
            plan(doc_of(with_params), registry)
        with_splits = {
            "events": NodeSpec(uses="synth-events", params={"start_ms": "$splits.t1"})
        }
        from dskit.pipeline.base import TimeSplitConfig as TSC

        with pytest.raises(ConfigError, match="fully literal"):
            plan(doc_of(with_splits, splits=TSC(**BANKING_SPLITS)), registry)

    def test_ungated_capital_refuses_to_plan(self, registry):
        pipeline = banking_pipeline()
        pipeline["size"] = NodeSpec(
            uses="synth-capital",
            inputs={"signal": "$qhat.signal", "survivors": "$family.instruments"},
            params={"bankroll": 100.0},
        )
        with pytest.raises(ConfigError, match="un-gated capital"):
            plan(doc_of(pipeline, splits=TimeSplitConfig(**BANKING_SPLITS)), registry)

    def test_score_must_declare_its_split(self, registry):
        pipeline = {
            "events": NodeSpec(uses="synth-events"),
            "labels": NodeSpec(
                uses="synth-labels", inputs={"events": "$events.events"}
            ),
            "mkt": NodeSpec(uses="synth-market", inputs={"events": "$events.events"}),
            "score": NodeSpec(
                uses="synth-score",
                inputs={
                    "events": "$events.events",
                    "signal": "$mkt.signal",
                    "outcomes": "$labels.outcomes",
                },
            ),
        }
        with pytest.raises(ConfigError, match="must declare which split"):
            plan(doc_of(pipeline), registry)

    def test_test_split_score_must_be_terminal(self, registry):
        splits = TimeSplitConfig(**BANKING_SPLITS)
        base = {
            "events": NodeSpec(uses="synth-events"),
            "labels": NodeSpec(
                uses="synth-labels", inputs={"events": "$events.events"}
            ),
            "mkt": NodeSpec(uses="synth-market", inputs={"events": "$events.events"}),
            "final": NodeSpec(
                uses="synth-score",
                inputs={
                    "events": "$events.events",
                    "signal": "$mkt.signal",
                    "outcomes": "$labels.outcomes",
                },
                params={"split": "test"},
            ),
        }
        terminal = dict(base)
        terminal["rep"] = NodeSpec(
            uses="synth-report", inputs={"metrics": "$final.metrics"}
        )
        plan(doc_of(terminal, splits=splits), registry)
        leaky = dict(base)
        leaky["rescored"] = NodeSpec(
            uses="synth-report", inputs={"metrics": "$final.metrics"}
        )
        leaky["edge_test"] = NodeSpec(
            uses="stat_test", inputs={"scores": "$final.cluster_scores"}
        )
        with pytest.raises(ConfigError, match="terminal evaluation"):
            plan(doc_of(leaky, splits=splits), registry)

    def test_mode_only_on_trainable_roles(self, registry):
        pipeline = {
            "events": NodeSpec(uses="synth-events"),
            "clip": NodeSpec(
                uses="synth-clip",
                inputs={"events": "$events.events"},
                mode="load",
                artifact="x.json",
            ),
        }
        with pytest.raises(ConfigError, match="trainable roles"):
            plan(doc_of(pipeline), registry)


class CausalSource(Node):
    """A source whose records are a time series — it declares the split
    kinds it may lawfully live under."""

    role = "data"
    outputs = ("records",)
    supported_split_kinds = ("time", "trailing")

    def run(self, ctx, inputs):
        return {"records": []}


class SplitCapital(Node):
    """A capital node that REPLAYS over a split (so it accepts the knob and
    defaults it) — unlike a generic budgeted selection, which has no time
    dimension and must stay exempt."""

    role = "capital"
    outputs = ("positions",)
    _PARAMS = ("bankroll", "split")

    def run(self, ctx, inputs):
        return {"positions": {}}


def _causal_pipeline(**capital_params):
    return {
        "src": NodeSpec(uses="causal-source"),
        "gate": NodeSpec(uses="stat_test", inputs={"scores": "$src.records"}),
        "size": NodeSpec(
            uses="split-capital",
            inputs={"records": "$src.records", "survivors": "$gate.survivors"},
            params={"bankroll": 100.0, **capital_params},
        ),
    }


@pytest.fixture
def causal_registry(registry):
    registry.register("causal-source", CausalSource)
    registry.register("split-capital", SplitCapital)
    return registry


class TestCausalSplitDoctrine:
    """docs/24 always said a causal venue refuses a `random` split. The
    stage list enforced it; the node map had nowhere to say it, so a
    randomized cut over settled events planned clean — putting the test
    set's future inside the calibrator's past."""

    def test_a_random_cut_over_a_causal_source_refuses_to_plan(self, causal_registry):
        doc = doc_of(
            _causal_pipeline(split="test"),
            splits=RandomSplitSpec(train_frac=0.6, val_frac=0.2, seed=1),
        )
        with pytest.raises(ConfigError, match="unsound for CausalSource"):
            plan(doc, causal_registry)

    def test_a_time_cut_over_the_same_document_plans(self, causal_registry):
        doc = doc_of(
            _causal_pipeline(split="test"), splits=TimeSplitConfig(**BANKING_SPLITS)
        )
        assert "src" in plan(doc, causal_registry).order

    def test_a_node_with_no_opinion_is_never_second_guessed(self, registry):
        """The default is None — most nodes have no view, and a randomized
        cut is perfectly lawful for a cross-sectional study."""
        doc = doc_of(
            banking_pipeline(),
            splits=RandomSplitSpec(train_frac=0.6, val_frac=0.2, seed=1),
        )
        assert plan(doc, registry).order


class TestCapitalNeedsSplits:
    def test_a_split_accepting_capital_node_without_splits_refuses(
        self, causal_registry
    ):
        """The silent hole: `split` DEFAULTS, so the document need never
        write the word — and then no splits section is required, and the
        node keeps every record. A whole-history in-sample run that plans,
        runs and exits 0."""
        with pytest.raises(ConfigError, match="splits section required"):
            plan(doc_of(_causal_pipeline()), causal_registry)

    def test_a_broken_allowed_hook_does_not_take_the_planner_down(self, registry):
        """Knob discovery is a convenience for this rule, not its subject —
        a class whose `_allowed()` explodes still gets planned (and fails
        on its own terms elsewhere), rather than turning one bad hook into
        an unplannable document."""

        class BrokenAllowed(Node):
            role = "capital"
            outputs = ("positions",)

            @classmethod
            def _allowed(cls):
                raise RuntimeError("boom")

            def run(self, ctx, inputs):
                return {"positions": {}}

        registry.register("broken-allowed", BrokenAllowed)
        pipeline = {
            "events": NodeSpec(uses="synth-events"),
            "gate": NodeSpec(uses="stat_test", inputs={"scores": "$events.events"}),
            "size": NodeSpec(
                uses="broken-allowed", inputs={"survivors": "$gate.survivors"}
            ),
        }
        assert plan(doc_of(pipeline), registry).order

    def test_a_capital_node_with_no_split_knob_stays_exempt(self, registry):
        """A generic budgeted selection has no time dimension — it must not
        be dragged into needing a splits section it has no use for."""
        pipeline = {
            "events": NodeSpec(uses="synth-events"),
            "gate": NodeSpec(uses="stat_test", inputs={"scores": "$events.events"}),
            "size": NodeSpec(
                uses="synth-capital",
                inputs={"survivors": "$gate.survivors"},
                params={"bankroll": 100.0},
            ),
        }
        assert plan(doc_of(pipeline), registry).order


class TestCalBandRule:
    """ADR-0034: a `split: "cal"` reader refuses unless the declared
    splits can actually yield a cal band — a reader of a band that cannot
    exist would score zero rows and exit 0."""

    def _doc(self, splits):
        pipeline = banking_pipeline()
        spec = pipeline["validate"]
        pipeline["validate"] = NodeSpec(
            uses=spec.uses,
            inputs=dict(spec.inputs),
            params={**spec.params, "split": "cal"},
        )
        return doc_of(pipeline, splits=splits)

    def test_cal_reader_plans_over_a_declared_time_band(self, registry):
        splits = TimeSplitConfig(
            train_end_ms=BANKING_SPLITS["train_end_ms"],
            cal_start_ms=BANKING_SPLITS["val_end_ms"] - 1,
            val_end_ms=BANKING_SPLITS["val_end_ms"],
            test_end_ms=BANKING_SPLITS["test_end_ms"],
        )
        assert plan(self._doc(splits), registry).order

    def test_cal_reader_refuses_when_time_declares_no_band(self, registry):
        with pytest.raises(ConfigError, match="declared cal band"):
            plan(self._doc(TimeSplitConfig(**BANKING_SPLITS)), registry)

    def test_cal_reader_refuses_under_a_random_split(self, registry):
        splits = RandomSplitSpec(train_frac=0.6, val_frac=0.2, seed=1)
        with pytest.raises(ConfigError, match="declared cal band"):
            plan(self._doc(splits), registry)


class TestStatTestWeightsRule:
    """The plan-time mirror of the weights-port rules (ADR-0033): a
    weighted correction with no weights wire — or the converse — is
    knowable from the spec alone, so it refuses before anything runs."""

    def _pipeline(self, gate):
        return {"events": NodeSpec(uses="synth-events"), "gate": gate}

    def test_weighted_correction_without_a_weights_wire_refuses(self, registry):
        gate = NodeSpec(
            uses="stat_test",
            inputs={"scores": "$events.events"},
            params={"correction": "weighted-bh"},
        )
        with pytest.raises(ConfigError, match="wire a weights input"):
            plan(doc_of(self._pipeline(gate)), registry)

    def test_weighted_correction_with_the_wire_plans(self, registry):
        gate = NodeSpec(
            uses="stat_test",
            inputs={"scores": "$events.events", "weights": "$events.events"},
            params={"correction": "weighted-bh"},
        )
        assert plan(doc_of(self._pipeline(gate)), registry).order

    def test_a_weights_wire_with_an_unweighted_correction_refuses(self, registry):
        gate = NodeSpec(
            uses="stat_test",
            inputs={"scores": "$events.events", "weights": "$events.events"},
        )
        with pytest.raises(ConfigError, match="does not use weights"):
            plan(doc_of(self._pipeline(gate)), registry)


class TestSearchRules:
    def base(self):
        splits = TimeSplitConfig(**BANKING_SPLITS)
        pipeline = {
            "events": NodeSpec(uses="synth-events"),
            "labels": NodeSpec(
                uses="synth-labels", inputs={"events": "$events.events"}
            ),
            "qhat": NodeSpec(
                uses="synth-train",
                inputs={"events": "$events.events"},
                params={"min_train": 5},
            ),
            "validate": NodeSpec(
                uses="synth-score",
                inputs={
                    "events": "$events.events",
                    "signal": "$qhat.signal",
                    "outcomes": "$labels.outcomes",
                },
                params={"split": "val"},
            ),
        }
        return pipeline, splits

    def search_spec(self, objective="$validate.metrics.loss", space=None):
        return NodeSpec(
            uses="synth-search",
            params={
                "space": space if space is not None else {"qhat.min_train": [5, 10]},
                "objective": objective,
                "select": "min",
            },
        )

    def test_wellformed_search_plans(self, registry):
        pipeline, splits = self.base()
        pipeline["search"] = self.search_spec()
        plan(doc_of(pipeline, splits=splits), registry)

    def test_objective_must_reference_a_val_score(self, registry):
        pipeline, splits = self.base()
        pipeline["search"] = self.search_spec(objective="$qhat.signal")
        with pytest.raises(ConfigError, match="must come from a 'score' node"):
            plan(doc_of(pipeline, splits=splits), registry)
        pipeline, splits = self.base()
        pipeline["validate"] = NodeSpec(
            uses="synth-score",
            inputs=pipeline["validate"].inputs,
            params={"split": "train"},
        )
        pipeline["search"] = self.search_spec()
        with pytest.raises(ConfigError, match="validation only"):
            plan(doc_of(pipeline, splits=splits), registry)

    def test_space_must_address_declared_nodes(self, registry):
        pipeline, splits = self.base()
        pipeline["search"] = self.search_spec(space={"ghost.lr": [0.1]})
        with pytest.raises(ConfigError, match="no node 'ghost'"):
            plan(doc_of(pipeline, splits=splits), registry)

    def test_space_may_not_re_aim_the_score_node(self, registry):
        # The S1 exploit this rule exists for: {"validate.split": ["test"]}
        # planned clean, and selection consulted the split it must never
        # see. The score node is the ruler; the search may not re-aim it.
        pipeline, splits = self.base()
        pipeline["search"] = self.search_spec(space={"validate.split": ["test"]})
        with pytest.raises(ConfigError, match="may not address 'validate.split'"):
            plan(doc_of(pipeline, splits=splits), registry)

    def test_space_may_not_address_verdict_roles(self, registry):
        # stat_test and gate are the deploy verdict — a space that can
        # re-tune them is optimizing the ruler, not the model.
        pipeline, splits = self.base()
        pipeline["edge"] = NodeSpec(
            uses="stat_test", inputs={"scores": "$validate.cluster_scores"}
        )
        pipeline["search"] = self.search_spec(space={"edge.alpha": [0.01, 0.1]})
        with pytest.raises(ConfigError, match="may not address 'edge.alpha'"):
            plan(doc_of(pipeline, splits=splits), registry)

        pipeline, splits = self.base()
        pipeline["bank"] = NodeSpec(
            uses="synth-bank", inputs={"events": "$events.events"}
        )
        pipeline["family"] = NodeSpec(
            uses="synth-eligibility",
            inputs={"counts": "$bank.counts"},
            params={"min_events": 10},
        )
        pipeline["search"] = self.search_spec(space={"family.min_events": [5, 10]})
        with pytest.raises(ConfigError, match="may not address 'family.min_events'"):
            plan(doc_of(pipeline, splits=splits), registry)

    def test_space_may_not_address_fingerprinted_identity(self, registry):
        # data/labels params are hashed into the run identity at resolve;
        # a trial overriding them would rebuild the source and consume
        # data the identity never saw — the search-door twin of S4-F4.
        pipeline, splits = self.base()
        pipeline["events"] = NodeSpec(uses="synth-events", params={"n_events": 32})
        pipeline["search"] = self.search_spec(space={"events.n_events": [16, 32]})
        with pytest.raises(ConfigError, match="may not address 'events.n_events'"):
            plan(doc_of(pipeline, splits=splits), registry)

    def test_space_head_must_be_an_ancestor_of_the_objective(self, registry):
        # A head outside ancestors(objective) is a knob the search cannot
        # turn: the trial value never moves the objective, and the winner
        # override is never re-applied to the live run.
        pipeline, splits = self.base()
        pipeline["shadow"] = NodeSpec(
            uses="synth-train",
            inputs={"events": "$events.events"},
            params={"min_train": 5},
        )
        pipeline["search"] = self.search_spec(space={"shadow.min_train": [5, 10]})
        with pytest.raises(ConfigError, match="not an ancestor of the objective"):
            plan(doc_of(pipeline, splits=splits), registry)


class TestPlanTimeParams:
    def test_literal_params_validate_at_plan(self, registry):
        pipeline = {
            "events": NodeSpec(uses="synth-events", params={"n_events": 0}),
        }
        with pytest.raises(ConfigError, match="n_events"):
            plan(doc_of(pipeline), registry)

    def test_prev_defaults_substitute_before_validation(self, registry):
        pipeline = banking_pipeline()
        pipeline["size"] = NodeSpec(
            uses="synth-capital",
            inputs=pipeline["size"].inputs,
            params={"bankroll": {"$prev": "size.final_bankroll", "default": -5.0}},
        )
        with pytest.raises(ConfigError, match="bankroll"):
            plan(banking_document(pipeline=pipeline), registry)

    def test_node_ref_params_defer_to_execute(self, registry):
        pipeline = {
            "events": NodeSpec(uses="synth-events"),
            "rep": NodeSpec(uses="synth-report", params={"n": "$events.newest_ms"}),
        }
        p = plan(doc_of(pipeline), registry)
        assert p.deferred_params == ("rep",)
        assert p.to_obj()["nodes"]["rep"]["params_validation"] == "deferred"


def test_clock_documents_plan_with_a_warning(registry):
    from dskit.pipeline.document import ClockConfig

    d = banking_document(clock=ClockConfig(increment="epoch"))
    p = plan(d, registry)
    assert any("I-222" in w for w in p.warnings)
