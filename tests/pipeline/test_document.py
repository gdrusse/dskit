"""The node-map document grammar (docs/24 §2–§4, §6): shape, refs, hash."""

from types import SimpleNamespace

import pytest

from dskit.pipeline.base import ConfigError, TimeSplitConfig
from dskit.pipeline.document import (
    ClockConfig,
    NodeSpec,
    PipelineDocument,
    RandomSplitSpec,
    ScheduleConfig,
    TrailingSplitSpec,
    doc_split_from_obj,
    flatten_param_paths,
    is_node_ref,
    is_prev_ref,
    load_document,
    parse_node_ref,
    parse_prev_ref,
    save_document,
)


def doc(**overrides):
    """A minimal valid document: one source node, overridable per test."""
    base = {
        "name": "unit-test",
        "pipeline": {"source": NodeSpec(uses="synthetic-frame")},
    }
    base.update(overrides)
    return PipelineDocument(**base)


# ---------------------------------------------------------------------------
# Reference grammar
# ---------------------------------------------------------------------------


class TestRefGrammar:
    def test_parse_node_ref_splits_source_and_path(self):
        assert parse_node_ref("$edge_test.survivors") == ("edge_test", ("survivors",))
        assert parse_node_ref("$validate.metrics.loss") == (
            "validate",
            ("metrics", "loss"),
        )

    @pytest.mark.parametrize(
        "bad", ["$", "$node", "$node.", "$.out", "$Node.out", "$a..b", "no-dollar"]
    )
    def test_parse_node_ref_refuses_bad_grammar(self, bad):
        with pytest.raises(ConfigError):
            parse_node_ref(bad)

    def test_is_node_ref_detects_form_only(self):
        assert is_node_ref("$anything")  # malformed but $-shaped: routed to parser
        assert not is_node_ref("literal")
        assert not is_node_ref(3.0)

    def test_parse_prev_ref_returns_target_and_default(self):
        node, path, default = parse_prev_ref(
            {"$prev": "replay.final_bankroll", "default": 1000.0}
        )
        assert (node, path, default) == ("replay", ("final_bankroll",), 1000.0)

    def test_prev_ref_requires_default(self):
        with pytest.raises(ConfigError, match="default.*required"):
            parse_prev_ref({"$prev": "qhat.artifact"})

    def test_prev_ref_refuses_extra_keys(self):
        with pytest.raises(ConfigError, match="exactly the keys"):
            parse_prev_ref({"$prev": "a.b", "default": 0, "extra": 1})

    @pytest.mark.parametrize("bad", ["nodeless", "node.", ".out", 7])
    def test_prev_ref_refuses_bad_target(self, bad):
        with pytest.raises(ConfigError, match=r"\$prev must target"):
            parse_prev_ref({"$prev": bad, "default": None})

    def test_is_prev_ref_detects_the_key(self):
        assert is_prev_ref({"$prev": "a.b", "default": 0})
        assert is_prev_ref({"$prev": "half-formed"})  # routed to parser, refused there
        assert not is_prev_ref({"default": 0})


class TestFlattenParamPaths:
    """The override-target grammar read FORWARDS: one node's params as the
    ``"<node>.<param.path>"`` keys an override (or a search space) uses."""

    def test_nested_params_flatten_to_dotted_paths(self):
        flat = flatten_param_paths(
            "qhat",
            {"hidden_size": 32, "model": {"kind": "gru", "layers": {"depth": 2}}},
        )
        assert flat == {
            "qhat.hidden_size": 32,
            "qhat.model.kind": "gru",
            "qhat.model.layers.depth": 2,
        }

    def test_leaves_keep_their_native_values(self):
        # Sinks own rendering; the core never stringifies a knob (a filter
        # on hidden_size == 32 should compare numbers, not "32").
        flat = flatten_param_paths("clip", {"lo": 0.02, "on": True, "cuts": [1, 2]})
        assert flat == {"clip.lo": 0.02, "clip.on": True, "clip.cuts": [1, 2]}

    def test_descent_stops_where_an_override_cannot_go(self):
        # An override navigates existing dict keys only, so a $prev carry,
        # an empty dict and a dict with non-segment keys are LEAVES —
        # descending would emit a key no override could ever address.
        carry = {"$prev": "size.final_bankroll", "default": 1000.0}
        flat = flatten_param_paths(
            "size", {"bankroll": carry, "empty": {}, "by_day": {"1d": 3}}
        )
        assert flat == {
            "size.bankroll": carry,
            "size.empty": {},
            "size.by_day": {"1d": 3},
        }

    def test_unaddressable_top_level_names_are_dropped(self):
        assert flatten_param_paths("size", {"1d": 3, "ok": 1}) == {"size.ok": 1}

    def test_no_params_flatten_to_nothing(self):
        assert flatten_param_paths("bank", {}) == {}


# ---------------------------------------------------------------------------
# NodeSpec shape
# ---------------------------------------------------------------------------


class TestNodeSpec:
    def test_minimal_and_full_forms_construct(self):
        NodeSpec(uses="event-bank")
        NodeSpec(
            uses="myml.nodes:TorchMLP",
            inputs={"frame": "$features.frame"},
            params={
                "lr": 3e-4,
                "warm_start": {"$prev": "train.artifact", "default": None},
                "objective": "$validate.metrics.loss",
            },
            every="once",
            mode="train",
            role="train",
            notes="doc",
        )

    @pytest.mark.parametrize("bad", ["", "Not-A-Kind", "has space", "1leading"])
    def test_uses_grammar_refused(self, bad):
        with pytest.raises(ConfigError):
            NodeSpec(uses=bad)

    def test_uses_accepts_kind_and_class_ref_forms(self):
        NodeSpec(uses="hpo-grid")
        NodeSpec(uses="pkg.module:ClassName")

    def test_inputs_must_be_dollar_refs(self):
        with pytest.raises(ConfigError, match="must be a '\\$node.output' reference"):
            NodeSpec(uses="filter", inputs={"records": "literal"})

    def test_inputs_refuse_prev_carries(self):
        with pytest.raises(ConfigError, match="only legal inside params"):
            NodeSpec(uses="filter", inputs={"x": {"$prev": "a.b", "default": 0}})

    def test_bad_port_name_refused(self):
        with pytest.raises(ConfigError, match="port names"):
            NodeSpec(uses="filter", inputs={"Bad-Port": "$a.b"})

    def test_params_refs_validated_recursively(self):
        with pytest.raises(ConfigError, match=r"params.where\[1\]"):
            NodeSpec(uses="filter", params={"where": ["$ok.ref", "$broken."]})

    def test_mode_load_requires_artifact(self):
        with pytest.raises(ConfigError, match="requires a pinned 'artifact'"):
            NodeSpec(uses="train-kind", mode="load")
        NodeSpec(uses="train-kind", mode="load", artifact="runs/x/model.pt")

    def test_mode_train_refuses_pinned_artifact(self):
        with pytest.raises(ConfigError, match="contradictory"):
            NodeSpec(uses="train-kind", mode="train", artifact="model.pt")

    def test_artifact_without_mode_refused(self):
        with pytest.raises(ConfigError, match="without mode 'load'"):
            NodeSpec(uses="train-kind", artifact="model.pt")

    def test_bad_every_mode_role_refused(self):
        with pytest.raises(ConfigError) as exc:
            NodeSpec(uses="k", every="hourly", mode="test", role="banker")
        assert len(exc.value.errors) == 3

    def test_refs_gathers_inputs_and_params(self):
        spec = NodeSpec(
            uses="k",
            inputs={"a": "$up.out"},
            params={
                "cut": "$splits.t1",
                "bank": {"$prev": "replay.final_bankroll", "default": 1.0},
            },
        )
        node_refs, prev_refs = spec.refs()
        assert ("up", ("out",)) in node_refs
        assert ("splits", ("t1",)) in node_refs
        assert prev_refs == [("replay", ("final_bankroll",), 1.0)]

    def test_from_obj_rejects_unknown_keys_and_round_trips(self):
        with pytest.raises(ConfigError, match="unknown key"):
            NodeSpec.from_obj({"uses": "k", "stage": "train"})
        spec = NodeSpec(uses="k", params={"x": 1}, mode="load", artifact="a")
        assert NodeSpec.from_obj(spec.to_obj()) == spec


# ---------------------------------------------------------------------------
# Splits (spec §6)
# ---------------------------------------------------------------------------


class TestRandomSplitSpec:
    def test_zero_test_split_is_legal(self):
        s = RandomSplitSpec(train_frac=0.8, val_frac=0.2)
        assert not s.has_test

    def test_float_dust_does_not_create_a_test_split(self):
        # 0.7 + 0.3 lands a hair below 1.0 in IEEE arithmetic.
        s = RandomSplitSpec(train_frac=0.7, val_frac=0.3, seed=3)
        assert not s.has_test
        assigned = {s.split_of(SimpleNamespace(cluster=f"c{i}")) for i in range(500)}
        assert assigned == {"train", "val"}

    def test_sum_above_one_refused(self):
        with pytest.raises(ConfigError, match="must not exceed 1.0"):
            RandomSplitSpec(train_frac=0.8, val_frac=0.3)

    def test_three_way_assignment_when_test_exists(self):
        s = RandomSplitSpec(train_frac=0.5, val_frac=0.25, seed=1)
        assert s.has_test
        assigned = {s.split_of(SimpleNamespace(cluster=f"c{i}")) for i in range(500)}
        assert assigned == {"train", "val", "test"}

    def test_assignment_is_deterministic_and_seed_sensitive(self):
        a = RandomSplitSpec(train_frac=0.5, val_frac=0.25, seed=1)
        b = RandomSplitSpec(train_frac=0.5, val_frac=0.25, seed=2)
        rec = SimpleNamespace(cluster="EVT-1")
        assert a.split_of(rec) == a.split_of(rec)
        diverges = any(
            a.split_of(SimpleNamespace(cluster=f"c{i}"))
            != b.split_of(SimpleNamespace(cluster=f"c{i}"))
            for i in range(64)
        )
        assert diverges


class TestTrailingSplitSpec:
    def test_materialize_counts_backward_from_newest(self):
        day = 24 * 60 * 60 * 1000
        cuts = TrailingSplitSpec(test_days=14, val_days=28).materialize(100 * day)
        assert isinstance(cuts, TimeSplitConfig)
        assert cuts.test_end_ms == 100 * day
        assert cuts.val_end_ms == 86 * day
        assert cuts.train_end_ms == 58 * day

    def test_materialize_refuses_shallow_history_and_bad_types(self):
        s = TrailingSplitSpec(test_days=14, val_days=28)
        with pytest.raises(ValueError, match="deep enough"):
            s.materialize(1000)
        with pytest.raises(ValueError, match="epoch ms"):
            s.materialize(True)

    def test_train_days_all_prior_or_positive_int(self):
        TrailingSplitSpec(test_days=1, val_days=1, train_days="all-prior")
        TrailingSplitSpec(test_days=1, val_days=1, train_days=90)
        with pytest.raises(ConfigError):
            TrailingSplitSpec(test_days=1, val_days=1, train_days=0)

    def test_dispatch_covers_all_three_kinds(self):
        assert isinstance(
            doc_split_from_obj({"kind": "trailing", "test_days": 1, "val_days": 1}),
            TrailingSplitSpec,
        )
        assert isinstance(
            doc_split_from_obj(
                {"kind": "time", "train_end_ms": 1, "val_end_ms": 2, "test_end_ms": 3}
            ),
            TimeSplitConfig,
        )
        with pytest.raises(ConfigError, match="unknown kind"):
            doc_split_from_obj({"kind": "walkforward"})


# ---------------------------------------------------------------------------
# Clock + schedule
# ---------------------------------------------------------------------------


class TestClockAndSchedule:
    def test_clock_accepts_sentinels_and_epoch_ms(self):
        ClockConfig(increment="epoch")
        ClockConfig(increment="day", start=0, until=1_750_000_000_000)

    def test_clock_refuses_bad_fields(self):
        with pytest.raises(ConfigError) as exc:
            ClockConfig(increment="tick", start="soon", until=-1)
        assert len(exc.value.errors) == 3

    def test_schedule_is_loose_documentation_with_strict_keys(self):
        ScheduleConfig(cadence="weekly", day="MON", asof="run-date")
        with pytest.raises(ConfigError, match="unknown key"):
            ScheduleConfig.from_obj({"cadence": "weekly", "cron": "0 0 * * 1"})


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


class TestPipelineDocument:
    def test_minimal_document_constructs(self):
        doc()

    @pytest.mark.parametrize("bad", ["", "Has Spaces", "-leading", "UPPER"])
    def test_name_must_be_run_dir_safe(self, bad):
        with pytest.raises(ConfigError):
            doc(name=bad)

    def test_splits_is_a_reserved_node_key(self):
        with pytest.raises(ConfigError, match="reserved"):
            doc(pipeline={"splits": NodeSpec(uses="k")})

    def test_dangling_wire_refused_at_construction(self):
        with pytest.raises(ConfigError, match="dangling wire"):
            doc(
                pipeline={
                    "a": NodeSpec(uses="k", inputs={"x": "$ghost.out"}),
                }
            )

    def test_self_wire_refused_but_prev_self_reference_legal(self):
        with pytest.raises(ConfigError, match="cannot wire its own output"):
            doc(pipeline={"a": NodeSpec(uses="k", inputs={"x": "$a.out"})})
        doc(
            pipeline={
                "replay": NodeSpec(
                    uses="k",
                    params={
                        "bankroll": {"$prev": "replay.final_bankroll", "default": 1e3}
                    },
                )
            }
        )

    def test_prev_to_undeclared_node_refused(self):
        with pytest.raises(ConfigError, match=r"\$prev targets 'ghost'"):
            doc(
                pipeline={
                    "a": NodeSpec(
                        uses="k", params={"w": {"$prev": "ghost.out", "default": 0}}
                    )
                }
            )

    def test_splits_refs_and_split_params_require_the_section(self):
        with pytest.raises(ConfigError, match="no splits section"):
            doc(pipeline={"a": NodeSpec(uses="k", params={"cut": "$splits.t1"})})
        with pytest.raises(ConfigError, match="splits section required"):
            doc(pipeline={"a": NodeSpec(uses="k", params={"split": "val"})})
        doc(
            pipeline={"a": NodeSpec(uses="k", params={"split": "val"})},
            splits=RandomSplitSpec(train_frac=0.8, val_frac=0.2),
        )

    def test_every_requires_the_clock(self):
        with pytest.raises(ConfigError, match="only meaningful under a clock"):
            doc(pipeline={"a": NodeSpec(uses="k", every="epoch")})
        doc(
            pipeline={"a": NodeSpec(uses="k", every="epoch")},
            clock=ClockConfig(increment="epoch"),
        )

    def test_errors_accumulate_across_nodes(self):
        with pytest.raises(ConfigError) as exc:
            PipelineDocument.from_obj(
                {
                    "name": "multi-bad",
                    "pipeline": {
                        "a": {"uses": ""},
                        "b": {"uses": "k", "every": "hourly"},
                    },
                }
            )
        text = str(exc.value)
        assert "pipeline.a" in text and "pipeline.b" in text

    def test_from_obj_rejects_unknown_top_level_keys(self):
        with pytest.raises(ConfigError, match="unknown key"):
            PipelineDocument.from_obj({"name": "x", "pipeline": {}, "stages": []})

    def test_round_trip_preserves_identity(self):
        d = doc(
            splits=RandomSplitSpec(train_frac=0.8, val_frac=0.2),
            schedule=ScheduleConfig(cadence="weekly"),
        )
        assert PipelineDocument.from_obj(d.to_obj()).hash == d.hash


class TestDocumentHash:
    def test_env_outputs_schedule_and_notes_never_move_the_hash(self):
        from dskit.pipeline.base import EnvConfig, OutputsConfig

        bare = doc()
        dressed = doc(
            env=EnvConfig(env_file=".env", require=("API_KEY_ID",)),
            outputs=OutputsConfig(run_root="/tmp/elsewhere"),
            schedule=ScheduleConfig(cadence="weekly", day="MON"),
            notes="commentary",
        )
        assert bare.hash == dressed.hash

    def test_pipeline_splits_and_clock_do_move_the_hash(self):
        bare = doc()
        assert doc(pipeline={"other": NodeSpec(uses="k")}).hash != bare.hash
        assert (
            doc(splits=RandomSplitSpec(train_frac=0.8, val_frac=0.2)).hash != bare.hash
        )
        assert doc(clock=ClockConfig(increment="epoch")).hash != bare.hash

    def test_prev_reference_and_default_are_hash_material(self):
        a = doc(
            pipeline={
                "n": NodeSpec(
                    uses="k", params={"w": {"$prev": "n.out", "default": 1.0}}
                )
            }
        )
        b = doc(
            pipeline={
                "n": NodeSpec(
                    uses="k", params={"w": {"$prev": "n.out", "default": 2.0}}
                )
            }
        )
        assert a.hash != b.hash


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


class TestDocumentIO:
    def test_save_load_round_trip(self, tmp_path):
        d = doc(splits=RandomSplitSpec(train_frac=0.6, val_frac=0.2))
        path = tmp_path / "doc.json"
        save_document(d, path)
        assert load_document(path).hash == d.hash

    def test_bad_json_and_bad_shape_name_the_path(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("{not json")
        with pytest.raises(ValueError, match="broken.json"):
            load_document(path)
        path.write_text('["a", "list"]')
        with pytest.raises(ConfigError, match="must be a JSON object"):
            load_document(path)
        path.write_text('{"name": "x", "pipeline": {"a": {"uses": ""}}}')
        with pytest.raises(ConfigError, match="broken.json.*pipeline.a"):
            load_document(path)
