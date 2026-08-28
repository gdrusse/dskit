"""The ``foreach`` document section (ADR-0039): declared fan-out over a key list.

Two things are on trial here. The GRAMMAR — suffixing, reference rewrite,
the ``$each`` token and opt-in port fan-out — and the IDENTITY claim that
makes the grammar safe to ship: expansion is DERIVED, so every document
written before ``foreach`` existed hashes exactly as it did, while a
``foreach`` document's keys and template ARE identity.

The flagship is :class:`TestLonghandEquivalence`: a two-key ``foreach``
document expands to exactly the graph a hand-written longhand document
declares — node for node, port for port — and both run to the same
result. That is what proves the section is fan-out over the ONE execution
path, not a second one.
"""

import json
import os
import pathlib
import re
from dataclasses import replace

import pytest

from dskit.pipeline.__main__ import main
from dskit.pipeline.base import ConfigError, OutputsConfig
from dskit.pipeline.document import (
    _NODE_KEY_OK,
    EACH_TOKEN,
    FOREACH_SEP,
    SEARCH_SPACE_PARAM,
    ForeachSpec,
    NodeSpec,
    PipelineDocument,
    RandomSplitSpec,
    load_document,
    save_document,
)
from dskit.pipeline.driver import run_document
from dskit.pipeline.io import load_config
from dskit.pipeline.kinds_search import HpoGrid
from dskit.pipeline.planner import plan

ASOF = "2026-01-01"

EXAMPLES = pathlib.Path(__file__).parents[2] / "examples" / "pipeline"

#: Every shipped example's identity hash — node-map, ``foreach`` and the
#: one stage-list document alike — restated here INDEPENDENTLY of the
#: documents themselves (CLAUDE.md: a validation suite must not source
#: its expectation from its subject). ADR-0039's hard constraint is that
#: adding the ``foreach`` section moves none of them; this is what proves
#: it. The test ENUMERATES the directory and requires a pin for every
#: file it finds, so a new example that forgot its line fails here rather
#: than shipping unpinned: a new example ADDS a line, and an existing
#: line that has to change is a breaking identity move.
PINNED_EXAMPLE_HASHES = {
    "model-sweep.json": (
        "c01ae84ec899e1d80e5324d38e9262bceaa0438cde0a4e6bcf2700827c484493"
    ),
    "mpl-figure.json": (
        "e9d5f60c3676ca77a79a7406c7564387c394d3f3e3de3d5999fd5ffc957e2067"
    ),
    "nodemap-minimal.json": (
        "5ba8f4d62b2032f0584b17f60eb23c670986bd4a7029d57831aaed1f882498c7"
    ),
    "numpy-features.json": (
        "7df9b26e55fc3b8c36a9e2af15f00087aaf6370cad940ac23f7a74bc90b2fe12"
    ),
    "optuna-continuous.json": (
        "5560a479eacc071ec62a77251cdf4ba8b14e82ae5904c486b5ca50994cd3e5fd"
    ),
    "optuna-search.json": (
        "687f9292d7c908c27e96140ddab31a8ef7fb26f8a91a5a05e86e8feb53cdd67a"
    ),
    "pyomo-solve.json": (
        "f74da200f293f7d4dded5bc20efd1ce75dbfeae562ea6aeedb9e8918f1ef362a"
    ),
    "sb3-train.json": (
        "149bd150b5691ef941e65e6468656972cf0f025051147c1364e7def137adc961"
    ),
    "sklearn-fit.json": (
        "7355bfce12bf2128719f7b83cafec33c064f73d1cf28aca1d03699c2e4b0887e"
    ),
    "torch-declared.json": (
        "4039ddf167fa65dbf33211745bf28756fa51faa1d95f60f35c76b9e25250478b"
    ),
    "torch-train.json": (
        "0b14798b3146d98afe1d1dcf970c3c42fbff4ff2377dd5026c6aba4af3a7b5aa"
    ),
    "transformers-fit.json": (
        "523072fa7103fae26dc56a445414c88871548b759fb7fc94a9991a9cc600aa6c"
    ),
    "walk-forward.json": (
        "54197909aecaee056d055e1b1c5bc6c588ddca81073968057dafd97cb12fc482"
    ),
    "foreach-fanout.json": (
        "242120e437f7adc6be129c109d8bcd68909d8d620e892f7f5e1b1391836b88dd"
    ),
    # The one STAGE-LIST example: a different grammar with its own
    # loader and its own identity recipe, pinned here for the same
    # reason — an example nothing pins can move unnoticed.
    "synthetic.json": (
        "4351c116ab2271e20956bb1524abdc908bffedfbc4e10a02656154c01b420cda"
    ),
}

#: The shipped ``foreach`` example — the document the CLI tests drive.
FOREACH_EXAMPLE = str(EXAMPLES / "foreach-fanout.json")

#: The two synthetic instruments the fan-out keys name. Uppercase on
#: purpose: the RAW key is what ``$each`` substitutes into params, while
#: node keys take its slug, and a test that used already-lowercase keys
#: could not tell the two apart.
KEYS = ("SYNA", "SYNB")

#: What ``concat`` is told about provenance — one synthetic source cut
#: per instrument, so there is no second venue to tag.
WAIVER = "one synthetic source, fanned out per instrument"


def dataset_node():
    """The shared synthetic source both documents start from."""
    return NodeSpec(
        uses="dskit.pipeline.synthetic_nodes:SynthEvents",
        params={"n_events": 6, "n_instruments": 2, "seed": 3},
    )


def concat_params():
    """The shared ``concat`` node's knobs — identical in both documents."""
    return {"shape": "records", "provenance_waiver": WAIVER, "key": "contract"}


def foreach_document(**overrides):
    """The two-key ``foreach`` document: one filter template, fanned in."""
    base = {
        "name": "foreach-fanout",
        "pipeline": {
            "dataset": dataset_node(),
            "both": NodeSpec(
                uses="concat",
                inputs={"records__each": "$rows.records"},
                params=concat_params(),
            ),
        },
        "foreach": ForeachSpec(
            keys=list(KEYS),
            pipeline={
                "rows": NodeSpec(
                    uses="filter",
                    inputs={"records": "$dataset.events"},
                    params={
                        "where": [
                            {"field": "instrument", "op": "==", "value": EACH_TOKEN}
                        ]
                    },
                )
            },
        ),
    }
    base.update(overrides)
    return PipelineDocument(**base)


def longhand_document(**overrides):
    """The hand-written twin: the same graph, every node spelled out."""
    base = {
        "name": "foreach-fanout",
        "pipeline": {
            "dataset": dataset_node(),
            "both": NodeSpec(
                uses="concat",
                inputs={
                    "records__syna": "$rows__syna.records",
                    "records__synb": "$rows__synb.records",
                },
                params=concat_params(),
            ),
            "rows__syna": NodeSpec(
                uses="filter",
                inputs={"records": "$dataset.events"},
                params={
                    "where": [{"field": "instrument", "op": "==", "value": "SYNA"}]
                },
            ),
            "rows__synb": NodeSpec(
                uses="filter",
                inputs={"records": "$dataset.events"},
                params={
                    "where": [{"field": "instrument", "op": "==", "value": "SYNB"}]
                },
            ),
        },
    }
    base.update(overrides)
    return PipelineDocument(**base)


def message(exc):
    """One string holding every accumulated error of a ConfigError."""
    return " | ".join(exc.value.errors)


#: The grid the tuned documents below search, declared ONCE so the
#: ``foreach`` document and its longhand twin cannot drift apart in the
#: one place the whole search-space rule is about.
MIN_TRAIN_GRID = [1, 2]


def tuned_shared(space):
    """The shared nodes of a document tuning one template knob.

    Parameters
    ----------
    space : dict
        The search node's ``space``, the ONE thing the ``foreach`` form
        and its longhand twin spell differently.

    Returns
    -------
    dict
        Node key -> :class:`NodeSpec`: the source, its labels, the
        ``concat`` that fans the per-instance signals back into one
        table, the val-split score over that table, and the search node
        whose objective it is.
    """
    return {
        "dataset": NodeSpec(
            uses="dskit.pipeline.synthetic_nodes:SynthEvents",
            params={"n_events": 40, "n_instruments": 2, "seed": 3},
        ),
        "labels": NodeSpec(
            uses="dskit.pipeline.synthetic_nodes:SynthLabels",
            inputs={"events": "$dataset.events"},
        ),
        "signals": NodeSpec(
            uses="concat",
            inputs={"signal__each": "$qhat.signal"},
            params={"shape": "table"},
        ),
        "val": NodeSpec(
            uses="dskit.pipeline.synthetic_nodes:SynthScore",
            inputs={
                "events": "$dataset.events",
                "signal": "$signals.merged",
                "outcomes": "$labels.outcomes",
            },
            params={"split": "val", "min_events": 1},
        ),
        "tune": NodeSpec(
            uses="hpo-grid", params={"objective": "$val.metrics.loss", "space": space}
        ),
    }


def qhat_template():
    """The trainable template whose ``min_train`` the search overrides."""
    return NodeSpec(
        uses="dskit.pipeline.synthetic_nodes:SynthTrain",
        mode="train",
        inputs={"events": "$rows.records"},
        params={"min_train": 1},
    )


def rows_template():
    """The per-key filter template both tuned documents start from."""
    return NodeSpec(
        uses="filter",
        inputs={"records": "$dataset.events"},
        params={"where": [{"field": "instrument", "op": "==", "value": EACH_TOKEN}]},
    )


def tuned_document(**overrides):
    """A ``foreach`` document whose SHARED search node tunes the template.

    One space key names the template — ADR-0039's "search spaces come
    for free" — where the longhand twin below spells one key per key.
    """
    base = {
        "name": "tuned-fanout",
        "pipeline": tuned_shared({"qhat.min_train": MIN_TRAIN_GRID}),
        "splits": RandomSplitSpec(train_frac=0.8, val_frac=0.2),
        "foreach": ForeachSpec(
            keys=list(KEYS), pipeline={"rows": rows_template(), "qhat": qhat_template()}
        ),
    }
    base.update(overrides)
    return PipelineDocument(**base)


def tuned_longhand(**overrides):
    """The hand-written twin: two instances, and one space key per instance."""
    shared = tuned_shared(
        {
            "qhat__syna.min_train": MIN_TRAIN_GRID,
            "qhat__synb.min_train": MIN_TRAIN_GRID,
        }
    )
    shared["signals"] = NodeSpec(
        uses="concat",
        inputs={
            "signal__syna": "$qhat__syna.signal",
            "signal__synb": "$qhat__synb.signal",
        },
        params={"shape": "table"},
    )
    # Template-major, key-minor — the expansion's fixed emission order,
    # so the two node maps compare key for key and not just as sets.
    for each_key in KEYS:
        shared[f"rows__{each_key.lower()}"] = replace(
            rows_template(),
            params={"where": [{"field": "instrument", "op": "==", "value": each_key}]},
        )
    for each_key in KEYS:
        slug = each_key.lower()
        shared[f"qhat__{slug}"] = replace(
            qhat_template(), inputs={"events": f"$rows__{slug}.records"}
        )
    base = {
        "name": "tuned-fanout",
        "pipeline": shared,
        "splits": RandomSplitSpec(train_frac=0.8, val_frac=0.2),
    }
    base.update(overrides)
    return PipelineDocument(**base)


# ---------------------------------------------------------------------------
# The spec's own shape
# ---------------------------------------------------------------------------


class TestForeachSpecShape:
    def test_keys_are_sorted_and_pinned_as_a_tuple(self):
        spec = ForeachSpec(keys=["msft", "aapl"], pipeline={"t": NodeSpec(uses="x")})
        assert spec.keys == ("aapl", "msft")

    def test_an_empty_key_list_refuses_by_name(self):
        with pytest.raises(ConfigError) as exc:
            ForeachSpec(keys=[], pipeline={"t": NodeSpec(uses="x")})
        assert "foreach.keys" in message(exc)

    def test_a_dollar_prefixed_key_refuses_by_name(self):
        with pytest.raises(ConfigError) as exc:
            ForeachSpec(keys=["$window.records"], pipeline={"t": NodeSpec(uses="x")})
        assert "$window.records" in message(exc)
        assert "foreach.keys" in message(exc)

    def test_a_duplicate_key_refuses_by_name(self):
        with pytest.raises(ConfigError) as exc:
            ForeachSpec(keys=["a", "a"], pipeline={"t": NodeSpec(uses="x")})
        assert "'a'" in message(exc)

    def test_an_empty_template_pipeline_refuses(self):
        with pytest.raises(ConfigError) as exc:
            ForeachSpec(keys=["a"], pipeline={})
        assert "foreach.pipeline" in message(exc)

    def test_each_as_a_params_dict_key_refuses_by_name(self):
        with pytest.raises(ConfigError) as exc:
            ForeachSpec(
                keys=["a"],
                pipeline={"t": NodeSpec(uses="x", params={EACH_TOKEN: 1})},
            )
        assert EACH_TOKEN in message(exc)
        assert "key" in message(exc)

    def test_an_each_port_inside_a_template_refuses(self):
        with pytest.raises(ConfigError) as exc:
            ForeachSpec(
                keys=["a"],
                pipeline={
                    "t": NodeSpec(uses="x", inputs={"rows__each": "$other.records"})
                },
            )
        assert "rows__each" in message(exc)

    def test_a_template_may_not_take_the_reserved_splits_name(self):
        with pytest.raises(ConfigError) as exc:
            ForeachSpec(keys=["a"], pipeline={"splits": NodeSpec(uses="x")})
        assert "splits" in message(exc)

    def test_the_spec_round_trips_through_obj(self):
        spec = ForeachSpec(keys=["b", "a"], pipeline={"t": NodeSpec(uses="x")})
        assert ForeachSpec.from_obj(spec.to_obj()) == spec


# ---------------------------------------------------------------------------
# Expansion — the four rules
# ---------------------------------------------------------------------------


class TestExpansion:
    def test_suffixing_and_emission_order(self):
        doc = foreach_document()
        assert list(doc.expanded) == ["dataset", "both", "rows__syna", "rows__synb"]

    def test_foreach_groups_maps_template_key_to_instances(self):
        assert foreach_document().foreach_groups == {
            "rows": ("rows__syna", "rows__synb")
        }

    def test_reference_rewrite_inside_a_template(self):
        doc = PipelineDocument(
            name="rewrite",
            pipeline={"dataset": dataset_node()},
            foreach=ForeachSpec(
                keys=["a"],
                pipeline={
                    "rows": NodeSpec(
                        uses="filter", inputs={"records": "$dataset.events"}
                    ),
                    "clip": NodeSpec(
                        uses="dskit.pipeline.synthetic_nodes:SynthClip",
                        inputs={"events": "$rows.records"},
                        params={"carry": {"$prev": "rows.records", "default": []}},
                    ),
                },
            ),
        )
        clip = doc.expanded["clip__a"]
        assert clip.inputs == {"events": "$rows__a.records"}
        assert clip.params["carry"]["$prev"] == "rows__a.records"
        # A shared-node reference is NOT rewritten — only template keys are.
        assert doc.expanded["rows__a"].inputs == {"records": "$dataset.events"}

    def test_a_splits_reference_passes_untouched(self):
        doc = PipelineDocument(
            name="splitsref",
            pipeline={"dataset": dataset_node()},
            splits=RandomSplitSpec(train_frac=0.8, val_frac=0.2),
            foreach=ForeachSpec(
                keys=["a"],
                pipeline={
                    "rows": NodeSpec(
                        uses="filter",
                        inputs={"records": "$dataset.events"},
                        params={"cut": "$splits.seed"},
                    )
                },
            ),
        )
        assert doc.expanded["rows__a"].params["cut"] == "$splits.seed"

    def test_each_substitutes_whole_values_at_any_depth(self):
        doc = foreach_document()
        assert doc.expanded["rows__syna"].params["where"][0]["value"] == "SYNA"
        assert doc.expanded["rows__synb"].params["where"][0]["value"] == "SYNB"

    def test_each_is_never_substring_interpolated(self):
        doc = PipelineDocument(
            name="nosubstring",
            pipeline={"dataset": dataset_node()},
            foreach=ForeachSpec(
                keys=["a"],
                pipeline={
                    "rows": NodeSpec(
                        uses="filter",
                        inputs={"records": "$dataset.events"},
                        params={"tag": "prefix-" + EACH_TOKEN},
                    )
                },
            ),
        )
        assert doc.expanded["rows__a"].params["tag"] == "prefix-$each"

    def test_each_outside_a_template_is_legal_and_untouched(self):
        doc = PipelineDocument(
            name="outside",
            pipeline={
                "dataset": dataset_node(),
                "rows": NodeSpec(
                    uses="filter",
                    inputs={"records": "$dataset.events"},
                    params={"tag": EACH_TOKEN},
                ),
            },
        )
        assert doc.expanded["rows"].params["tag"] == EACH_TOKEN

    def test_port_fan_out_is_opt_in(self):
        doc = foreach_document()
        assert doc.expanded["both"].inputs == {
            "records__syna": "$rows__syna.records",
            "records__synb": "$rows__synb.records",
        }

    def test_the_port_and_node_spellings_share_one_separator(self):
        # The agreement FOREACH_SEP exists to keep: a fanned-out port and
        # the instance it wires must name the SAME key. Two constants
        # would be one edit away from a port that wires the wrong symbol.
        for port, ref in foreach_document().expanded["both"].inputs.items():
            slug = port.split(FOREACH_SEP)[-1]
            assert ref == "$rows" + FOREACH_SEP + slug + ".records"

    def test_a_shared_reference_to_a_template_key_without_each_refuses(self):
        with pytest.raises(ConfigError) as exc:
            foreach_document(
                pipeline={
                    "dataset": dataset_node(),
                    "both": NodeSpec(
                        uses="concat",
                        inputs={"records": "$rows.records"},
                        params=concat_params(),
                    ),
                }
            )
        assert "rows" in message(exc)
        assert "__each" in message(exc)

    def test_an_each_port_naming_no_template_key_refuses(self):
        with pytest.raises(ConfigError) as exc:
            foreach_document(
                pipeline={
                    "dataset": dataset_node(),
                    "both": NodeSpec(
                        uses="concat",
                        inputs={"records__each": "$dataset.events"},
                        params=concat_params(),
                    ),
                }
            )
        assert "records__each" in message(exc)
        assert "dataset" in message(exc)

    def test_a_collision_between_a_shared_key_and_an_instance_key_refuses(self):
        with pytest.raises(ConfigError) as exc:
            foreach_document(
                pipeline={
                    "dataset": dataset_node(),
                    "rows__syna": NodeSpec(
                        uses="filter", inputs={"records": "$dataset.events"}
                    ),
                    "both": NodeSpec(
                        uses="concat",
                        inputs={"records__each": "$rows.records"},
                        params=concat_params(),
                    ),
                }
            )
        assert "rows__syna" in message(exc)

    def test_a_template_key_colliding_with_a_shared_key_refuses(self):
        with pytest.raises(ConfigError) as exc:
            PipelineDocument(
                name="collide",
                pipeline={"dataset": dataset_node()},
                foreach=ForeachSpec(
                    keys=["a"], pipeline={"dataset": NodeSpec(uses="filter")}
                ),
            )
        assert "dataset" in message(exc)

    def test_two_keys_slugging_to_one_node_key_refuse(self):
        with pytest.raises(ConfigError) as exc:
            PipelineDocument(
                name="slugclash",
                pipeline={"dataset": dataset_node()},
                foreach=ForeachSpec(
                    keys=["BTC-USD", "btc_usd"],
                    pipeline={
                        "rows": NodeSpec(
                            uses="filter", inputs={"records": "$dataset.events"}
                        )
                    },
                ),
            )
        assert "rows__btc_usd" in message(exc)

    def test_the_pipeline_may_be_empty_when_a_foreach_is_declared(self):
        doc = PipelineDocument(
            name="templates-only",
            pipeline={},
            foreach=ForeachSpec(
                keys=["a", "b"],
                pipeline={"src": NodeSpec(uses="dskit.pipeline.synthetic_nodes:SynthEvents")},
            ),
        )
        assert list(doc.expanded) == ["src__a", "src__b"]

    def test_an_empty_pipeline_without_a_foreach_still_refuses(self):
        with pytest.raises(ConfigError):
            PipelineDocument(name="empty", pipeline={})

    def test_a_dangling_wire_inside_a_template_refuses(self):
        with pytest.raises(ConfigError) as exc:
            PipelineDocument(
                name="dangling",
                pipeline={"dataset": dataset_node()},
                foreach=ForeachSpec(
                    keys=["a"],
                    pipeline={
                        "rows": NodeSpec(
                            uses="filter", inputs={"records": "$nope.events"}
                        )
                    },
                ),
            )
        assert "nope" in message(exc)


# ---------------------------------------------------------------------------
# Identity — the point of the card
# ---------------------------------------------------------------------------


class TestIdentity:
    def test_the_derived_fields_never_reach_to_obj(self):
        obj = foreach_document().to_obj()
        assert "expanded" not in obj
        assert "foreach_groups" not in obj
        assert "foreach" in obj

    def test_a_document_without_foreach_emits_no_foreach_key(self):
        assert "foreach" not in longhand_document().to_obj()

    def test_expanded_is_the_pipeline_object_itself_without_a_foreach(self):
        doc = longhand_document()
        assert doc.expanded is doc.pipeline
        assert doc.foreach_groups == {}

    def test_the_shipped_example_hashes_are_unmoved(self):
        # ENUMERATE the directory, never the pin dict: a test that walked
        # its own expectations could not see the example nobody pinned,
        # and would claim a coverage it lacks.
        shipped = sorted(path.name for path in EXAMPLES.glob("*.json"))
        assert shipped == sorted(PINNED_EXAMPLE_HASHES), (
            "every document in examples/pipeline needs an identity pin here"
        )
        for name in shipped:
            path = EXAMPLES / name
            raw = json.loads(path.read_text(encoding="utf-8"))
            # The CLI's own sentinel, restated: a node map or a `foreach`
            # is the docs/24 grammar, anything else is the stage list.
            node_map = "pipeline" in raw or "foreach" in raw
            document = load_document(path) if node_map else load_config(path)
            assert document.hash == PINNED_EXAMPLE_HASHES[name], name

    def test_foreach_is_hash_material(self):
        one = foreach_document(
            foreach=ForeachSpec(
                keys=["SYNA"],
                pipeline={
                    "rows": NodeSpec(
                        uses="filter",
                        inputs={"records": "$dataset.events"},
                        params={
                            "where": [
                                {"field": "instrument", "op": "==", "value": EACH_TOKEN}
                            ]
                        },
                    )
                },
            )
        )
        assert one.hash != foreach_document().hash

    def test_notes_inside_a_foreach_are_not_hash_material(self):
        spec = foreach_document().foreach
        noted = ForeachSpec(
            keys=list(spec.keys), pipeline=dict(spec.pipeline), notes="why two keys"
        )
        assert foreach_document(foreach=noted).hash == foreach_document().hash

    def test_a_foreach_document_and_its_longhand_twin_hash_differently(self):
        # The graphs agree; the DECLARATIONS do not, and identity grades
        # what a document says. Adding a key must be a new identity.
        assert foreach_document().expanded == longhand_document().pipeline
        assert foreach_document().hash != longhand_document().hash

    def test_dataclasses_replace_re_derives_the_expansion(self):
        # Children call `replace(document, outputs=...)` to redirect a run
        # dir. The derived fields are init=False, so `replace` skips them
        # and __post_init__ rebuilds them — a pin, because a derived field
        # that `replace` carried STALE would run yesterday's graph.
        moved = replace(
            foreach_document(), outputs=OutputsConfig(run_root="/tmp/elsewhere")
        )
        assert moved.expanded == foreach_document().expanded
        assert moved.hash == foreach_document().hash  # placement is not identity

    def test_a_foreach_document_round_trips_through_json(self, tmp_path):
        path = tmp_path / "doc.json"
        save_document(foreach_document(), path)
        reloaded = load_document(path)
        assert reloaded.hash == foreach_document().hash
        # `save_document` sorts keys, so SHARED nodes come back in the
        # sorted declaration order the file now has; the instances still
        # follow, template-major and key-minor.
        assert reloaded.expanded == foreach_document().expanded
        assert list(reloaded.expanded)[-2:] == ["rows__syna", "rows__synb"]


# ---------------------------------------------------------------------------
# The flagship: longhand equivalence
# ---------------------------------------------------------------------------


class TestLonghandEquivalence:
    def test_expansion_equals_the_longhand_graph_node_for_node(self):
        expanded = foreach_document().expanded
        longhand = longhand_document().pipeline
        assert list(expanded) == list(longhand)
        for key, spec in longhand.items():
            assert expanded[key] == spec, key

    def test_both_plan_to_the_same_dag(self):
        a, b = plan(foreach_document()), plan(longhand_document())
        assert a.order == b.order
        assert a.edges == b.edges
        assert a.to_obj()["nodes"] == b.to_obj()["nodes"]

    def test_both_run_to_the_same_result(self, tmp_path):
        fan = run_document(
            foreach_document(outputs=OutputsConfig(run_root=str(tmp_path / "fan"))),
            asof=ASOF,
        )
        hand = run_document(
            longhand_document(outputs=OutputsConfig(run_root=str(tmp_path / "hand"))),
            asof=ASOF,
        )
        assert fan.exit_code == 0 and hand.exit_code == 0
        assert fan.outputs["both"]["merged"] == hand.outputs["both"]["merged"]
        assert len(fan.outputs["both"]["merged"]) == 12
        assert sorted(fan.node_states) == sorted(hand.node_states)


# ---------------------------------------------------------------------------
# The engine reads `expanded`
# ---------------------------------------------------------------------------


class TestEngineReadsExpanded:
    def test_plan_orders_the_expanded_graph(self):
        the_plan = plan(foreach_document())
        assert set(the_plan.order) == {"dataset", "rows__syna", "rows__synb", "both"}
        assert the_plan.order.index("both") > the_plan.order.index("rows__syna")

    def test_the_example_validates_and_reports_what_runs(self, capsys):
        assert main(["validate", FOREACH_EXAMPLE]) == 0
        out = capsys.readouterr().out
        assert "nodes: 4" in out
        assert "foreach" in out

    def test_the_example_plans_over_the_expanded_graph(self, capsys):
        assert main(["plan", FOREACH_EXAMPLE]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert "rows__syna" in payload["nodes"]

    def test_the_example_runs_end_to_end(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        assert main(["run", os.path.abspath(FOREACH_EXAMPLE), "--asof", ASOF]) == 0
        assert capsys.readouterr().out.startswith("**RAN")

    def test_a_foreach_only_document_is_not_mistaken_for_the_stage_grammar(
        self, tmp_path, capsys
    ):
        path = tmp_path / "templates-only.json"
        path.write_text(
            json.dumps(
                {
                    "name": "templates-only",
                    "pipeline": {},
                    "foreach": {
                        "keys": ["a"],
                        "pipeline": {
                            "src": {
                                "uses": "dskit.pipeline.synthetic_nodes:SynthEvents"
                            }
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        assert main(["validate", str(path)]) == 0
        assert "nodes: 1" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Search spaces come for free (ADR-0039)
# ---------------------------------------------------------------------------


def per_key_tuned_document(**overrides):
    """A document whose search node lives INSIDE the template.

    The other placement: one search per key, each tuning its own
    instance, where :func:`tuned_document` shares one search over all of
    them.
    """
    base = {
        "name": "tune-per-key",
        "pipeline": {
            "dataset": dataset_node(),
            "labels": NodeSpec(
                uses="dskit.pipeline.synthetic_nodes:SynthLabels",
                inputs={"events": "$dataset.events"},
            ),
            "market": NodeSpec(
                uses="dskit.pipeline.synthetic_nodes:SynthMarketSignal",
                inputs={"events": "$dataset.events"},
            ),
        },
        "splits": RandomSplitSpec(train_frac=0.8, val_frac=0.2),
        "foreach": ForeachSpec(
            keys=["a"],
            pipeline={
                "qhat": NodeSpec(
                    uses="dskit.pipeline.synthetic_nodes:SynthTrain",
                    mode="train",
                    inputs={"events": "$dataset.events"},
                    params={"min_train": 1},
                ),
                "val": NodeSpec(
                    uses="dskit.pipeline.synthetic_nodes:SynthScore",
                    inputs={
                        "events": "$dataset.events",
                        "signal": "$qhat.signal",
                        "baseline": "$market.signal",
                        "outcomes": "$labels.outcomes",
                    },
                    params={"split": "val", "min_events": 1},
                ),
                "tune": NodeSpec(
                    uses="hpo-grid",
                    params={
                        "objective": "$val.metrics.loss",
                        "space": {"qhat.min_train": MIN_TRAIN_GRID},
                    },
                ),
            },
        ),
    }
    base.update(overrides)
    return PipelineDocument(**base)


class TestSearchSpaceFanOut:
    """ADR-0039: a space key naming a template param expands per instance.

    A ``space`` key is an override PATH whose HEAD names a node, so a
    head naming a template is re-aimed exactly as a ``$``-reference is —
    at THIS instance inside a template, and at every instance in a
    shared node. That is what turns the N unpinned duplicate keys the
    ADR's context names into one declaration.
    """

    def test_a_shared_search_space_expands_to_every_instance(self):
        space = tuned_document().expanded["tune"].params[SEARCH_SPACE_PARAM]
        assert space == {
            "qhat__syna.min_train": MIN_TRAIN_GRID,
            "qhat__synb.min_train": MIN_TRAIN_GRID,
        }
        assert "qhat.min_train" not in space

    def test_the_expanded_space_is_the_longhand_twin_key_for_key(self):
        assert tuned_document().expanded == tuned_longhand().pipeline

    def test_a_shared_search_over_a_template_plans(self):
        the_plan = plan(tuned_document())
        assert the_plan.order == plan(tuned_longhand()).order
        assert "qhat__synb" in the_plan.ancestors("val")

    def test_a_space_key_inside_a_template_aims_at_its_own_instance(self):
        doc = per_key_tuned_document()
        assert doc.expanded["tune__a"].params["objective"] == "$val__a.metrics.loss"
        assert doc.expanded["tune__a"].params[SEARCH_SPACE_PARAM] == {
            "qhat__a.min_train": MIN_TRAIN_GRID
        }
        assert plan(doc).role_of("tune__a") == "search"

    def test_a_space_key_naming_a_shared_node_is_untouched(self):
        doc = tuned_document(pipeline=tuned_shared({"dataset.n_events": [20, 40]}))
        assert doc.expanded["tune"].params[SEARCH_SPACE_PARAM] == {
            "dataset.n_events": [20, 40]
        }

    def test_a_space_key_colliding_with_a_written_instance_key_refuses(self):
        with pytest.raises(ConfigError) as exc:
            tuned_document(
                pipeline=tuned_shared(
                    {"qhat.min_train": MIN_TRAIN_GRID, "qhat__syna.min_train": [3]}
                )
            )
        assert "qhat__syna.min_train" in message(exc)
        assert "qhat.min_train" in message(exc)

    def test_the_space_param_name_agrees_with_the_search_kind(self):
        # The document layer rewrites the key map the search kinds
        # actually read; two spellings would silently stop expanding.
        assert SEARCH_SPACE_PARAM in HpoGrid._PARAMS

    def test_both_tuned_documents_run_to_the_same_winner(self, tmp_path):
        fan = run_document(
            tuned_document(outputs=OutputsConfig(run_root=str(tmp_path / "fan"))),
            asof=ASOF,
        )
        hand = run_document(
            tuned_longhand(outputs=OutputsConfig(run_root=str(tmp_path / "hand"))),
            asof=ASOF,
        )
        assert fan.exit_code == 0 and hand.exit_code == 0
        assert fan.outputs["tune"]["best_params"] == hand.outputs["tune"]["best_params"]
        assert set(fan.outputs["tune"]["best_params"]) == {
            "qhat__syna.min_train",
            "qhat__synb.min_train",
        }
        assert fan.outputs["val"]["metrics"] == hand.outputs["val"]["metrics"]


# ---------------------------------------------------------------------------
# Generated names are node keys
# ---------------------------------------------------------------------------


class TestGeneratedInstanceNames:
    """The slug rule and the node-key grammar, pinned to each other.

    ``_SLUG_BAD`` restates the legal-character half of ``_NODE_KEY_OK``,
    and nothing else would notice if one moved: an instance key is
    GENERATED, so the declared-key check never reads it.
    """

    def test_hostile_keys_still_mint_legal_node_keys(self):
        doc = PipelineDocument(
            name="hostile-keys",
            pipeline={"dataset": dataset_node()},
            foreach=ForeachSpec(
                keys=["BTC-USD", "eth/usd", "2x", "a b", "Ünïcode"],
                pipeline={"rows": rows_template()},
            ),
        )
        for name in doc.expanded:
            assert re.match(_NODE_KEY_OK, name), name
        assert "rows__btc_usd" in doc.expanded

    def test_a_narrowed_key_grammar_refuses_the_generated_name(self, monkeypatch):
        # The runtime refusal that pins the two: narrow the node-key
        # grammar until the slug rule out-runs it, and the expansion must
        # say so BY NAME instead of minting a key the grammar forbids.
        monkeypatch.setattr("dskit.pipeline.document._NODE_KEY_OK", r"^[a-z]+$")
        with pytest.raises(ConfigError) as exc:
            PipelineDocument(
                name="narrowed",
                pipeline={"dataset": dataset_node()},
                foreach=ForeachSpec(keys=["a"], pipeline={"rows": rows_template()}),
            )
        assert "rows__a" in message(exc)

