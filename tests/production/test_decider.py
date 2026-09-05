"""§5.3 — the decider: one derived document, one deferred read, one head digest.

Three objects, three properties this file exists to pin:

1. **``serving_document`` DERIVES, it never restates.** Every value in
   the served document comes out of the run's own ``config.json`` and
   ``nodes/``: trainables flipped to ``load`` and pinned to
   ``artifacts/<key>``, search winners applied through the driver's own
   ``apply_param_override``, replayed gate/stat-test outputs read back
   from the run, the graph cut to ``ancestors(heads) ∪ heads``, and
   ``foreach``/``splits``/``env``/``tracking``/``outputs`` dropped. The
   function takes no knob that could disagree with the run.
2. **``Decider.prepare`` classifies BEFORE it constructs.** The structural
   pass resolves classes and edges, asks each class's pure
   ``serving_effect``, and refuses anything that is not the sole entry, a
   pure node or a manifest-backed ``release_read`` — without ever
   constructing the entry, scanning the store or materializing a split.
   The base pass then runs, once, only the needed nodes that are neither
   the entry nor its descendants.
3. **One mutable read per tick.** ``read_entry`` applies exactly ONE
   override and runs exactly the entry; ``evaluate`` seeds that frozen
   output and re-runs its descendants under a policy that DEFERS the
   entry, so a second mutable read cannot happen.

Pinned names this file introduces (see the group report):
``Decider(document, release, *, registry, adapter, proposer, clock)`` —
``document`` first, matching ``Arming(document, release, *, ...)``,
because the entry node/param/window, the heads and the replay map live in
``document.serving`` and the plan gives the Decider no other way to see
them; ``prepare(asof, base_run_dir) -> dict`` (the ``classify_plan``
answer) plus the ``serving_hash`` attribute; ``ServingExecutionPolicy(entry,
*, release, root)``; ``RecordedOutputs`` (role ``gate``) and
``RecordedStatTest`` (role ``stat_test``).
"""

from __future__ import annotations

import copy
import dataclasses
import inspect
import json
import os
from decimal import Decimal

import pytest

import dskit.onboarding.observations as observations_mod
import dskit.pipeline.driver as driver_mod
import dskit.pipeline.planner as planner_mod
from dskit.pipeline.document import NodeSpec, PipelineDocument
from dskit.pipeline.libs.observations import ObservationRows
from dskit.pipeline.node import SERVING_EFFECTS, Node
from dskit.pipeline.policy import ExecutionPolicy
from dskit.pipeline.records import MarketRecord
from dskit.production.base import ProductionError, canonical_hash
from dskit.production.decider import (
    PROPOSER_KINDS,
    Decider,
    IntentRows,
    Proposer,
    RecordedOutputs,
    RecordedStatTest,
    ServingExecutionPolicy,
    TargetPositions,
    serving_document,
)
from dskit.production.records import (
    AccountState,
    Candidate,
    EntryBatch,
    Position,
    Proposal,
    Provenance,
    Quote,
    RiskVersion,
)
from tests.production.conftest import (
    BASE_PASS_NODE,
    DAY_MS,
    ENTRY_NODE,
    ENTRY_PARAM,
    HEAD,
    LAST_ROW_MS,
    NOW_MS,
    SIDECAR,
    TRAINABLE_NODE,
    UNIVERSE,
    WINDOW_MS,
    SideTable,
    boom,
)

ASOF = "2026-01-06"
NO_REPLAY = {}


# --------------------------------------------------------------------------
# helpers — documents and run dirs shaped exactly like the driver's
# --------------------------------------------------------------------------


def variant(training_document, changes=None, drop=(), **overrides):
    """The conftest training document with nodes added, replaced or dropped."""
    pipeline = {k: v for k, v in training_document.pipeline.items() if k not in drop}
    pipeline.update(changes or {})
    fields = {
        "name": training_document.name,
        "pipeline": pipeline,
        "splits": training_document.splits,
    }
    fields.update(overrides)
    return PipelineDocument(**fields)


def fake_run_dir(tmp_path, document, records=None, carry=None, artifacts=()):
    """A run dir with the driver's own layout: config/nodes/carry/artifacts."""
    run = tmp_path / "run"
    (run / "nodes").mkdir(parents=True, exist_ok=True)
    (run / "config.json").write_text(json.dumps(document.to_obj()), encoding="utf-8")
    for i, (key, spec) in enumerate(document.expanded.items(), start=1):
        record = {
            "node": key,
            "uses": spec.uses,
            "role": None,
            "status": "ok",
            "seconds": 0.0,
            "outputs": {},
        }
        record.update((records or {}).get(key, {}))
        (run / "nodes" / f"{i:02d}-{key}.json").write_text(
            json.dumps(record), encoding="utf-8"
        )
    (run / "carry.json").write_text(json.dumps(carry or {}), encoding="utf-8")
    for key in artifacts:
        (run / "artifacts" / key).mkdir(parents=True, exist_ok=True)
    return str(run)


def derived(training_document, run_dir, heads=(HEAD,), replay=NO_REPLAY):
    """`serving_document` over the conftest training run."""
    return serving_document(training_document, run_dir, list(heads), replay)


def gate_node(inputs=None):
    """A `gate`-role node (`eligibility`) — the classic replayed verdict."""
    return NodeSpec(
        uses="eligibility",
        inputs=inputs or {"banked": f"${BASE_PASS_NODE}.table"},
        params={"min_events": 1},
    )


def gated_document(training_document):
    """The training document with a gate between `weights` and the head."""
    head = training_document.pipeline[HEAD]
    return variant(
        training_document,
        changes={
            "family": gate_node(),
            HEAD: dataclasses.replace(
                head, inputs={"records": "$scored.records", "weight": "$family.instruments"}
            ),
        },
    )


GATE_OUTPUTS = {"instruments": list(UNIVERSE), "verdict": "GO"}


# --------------------------------------------------------------------------
# serving_document — one case per §5.3 derivation rule
# --------------------------------------------------------------------------


class TestServingDocumentDerivation:
    def test_it_returns_a_pipeline_document(self, training_document, run_dir):
        assert isinstance(derived(training_document, run_dir), PipelineDocument)

    def test_a_trained_trainable_becomes_a_pinned_load(self, training_document, run_dir):
        spec = derived(training_document, run_dir).expanded[TRAINABLE_NODE]
        assert spec.mode == "load"
        assert spec.artifact == os.path.join(run_dir, "artifacts", TRAINABLE_NODE)

    def test_the_run_document_keeps_its_train_mode(self, training_document, run_dir):
        derived(training_document, run_dir)
        assert training_document.expanded[TRAINABLE_NODE].mode == "train"

    def test_a_trainable_with_no_artifact_dir_refuses(self, training_document, tmp_path):
        empty = fake_run_dir(tmp_path, training_document)
        with pytest.raises(ProductionError) as exc:
            derived(training_document, empty)
        assert TRAINABLE_NODE in str(exc.value)

    def test_every_other_node_keeps_its_params_verbatim(self, training_document, run_dir):
        out = derived(training_document, run_dir)
        for key, spec in out.expanded.items():
            if key == TRAINABLE_NODE:
                continue
            assert spec.params == training_document.expanded[key].params
            assert spec.uses == training_document.expanded[key].uses
            assert spec.inputs == training_document.expanded[key].inputs

    def test_it_is_cut_to_the_heads_and_their_ancestors(self, training_document, run_dir):
        out = derived(training_document, run_dir, heads=[TRAINABLE_NODE])
        assert set(out.expanded) == {ENTRY_NODE, "usable", "grid", TRAINABLE_NODE}
        assert HEAD not in out.expanded and BASE_PASS_NODE not in out.expanded

    def test_the_full_head_keeps_every_ancestor(self, training_document, run_dir):
        out = derived(training_document, run_dir)
        assert set(out.expanded) == set(training_document.expanded)

    def test_an_unknown_head_refuses(self, training_document, run_dir):
        with pytest.raises(ProductionError) as exc:
            derived(training_document, run_dir, heads=["nope"])
        assert "nope" in str(exc.value)

    def test_no_heads_refuses(self, training_document, run_dir):
        with pytest.raises(ProductionError):
            derived(training_document, run_dir, heads=[])

    def test_splits_are_dropped(self, training_document, run_dir):
        assert training_document.splits is not None
        assert derived(training_document, run_dir).splits is None

    def test_foreach_is_dropped(self, training_document, run_dir):
        assert derived(training_document, run_dir).foreach is None

    @pytest.mark.parametrize("section", ["env", "tracking", "outputs"])
    def test_the_placement_sections_are_dropped(self, training_document, run_dir, section):
        assert getattr(derived(training_document, run_dir), section) is None

    def test_a_needed_prev_reference_refuses(self, training_document, tmp_path):
        head = training_document.pipeline[HEAD]
        doc = variant(
            training_document,
            changes={
                HEAD: dataclasses.replace(
                    head,
                    params={**head.params, "how": {"$prev": "picks.how", "default": "strict"}},
                )
            },
        )
        run = fake_run_dir(tmp_path, doc, artifacts=[TRAINABLE_NODE])
        with pytest.raises(ProductionError) as exc:
            serving_document(doc, run, [HEAD], NO_REPLAY)
        assert "$prev" in str(exc.value) or "prev" in str(exc.value)

    def test_a_prev_reference_outside_the_cut_is_not_reached(
        self, training_document, tmp_path
    ):
        """Only NEEDED nodes are judged: a dropped node's `$prev` is nobody's."""
        doc = variant(
            training_document,
            changes={
                "tail": NodeSpec(
                    uses="filter",
                    inputs={"records": f"${HEAD}.records"},
                    params={"where": [{"field": "value", "op": ">",
                                       "value": {"$prev": "tail.bar", "default": 0}}]},
                )
            },
        )
        run = fake_run_dir(tmp_path, doc, artifacts=[TRAINABLE_NODE])
        out = serving_document(doc, run, [HEAD], NO_REPLAY)
        assert "tail" not in out.expanded


class TestServingDocumentIdentity:
    def test_the_derived_hash_is_stable(self, training_document, run_dir):
        one = derived(training_document, run_dir)
        two = derived(training_document, run_dir)
        assert one.hash == two.hash

    def test_the_derived_hash_differs_from_the_runs(self, training_document, run_dir):
        out = derived(training_document, run_dir)
        assert out.hash != training_document.hash

    def test_a_different_head_set_is_a_different_document(self, training_document, run_dir):
        one = derived(training_document, run_dir)
        two = derived(training_document, run_dir, heads=[TRAINABLE_NODE])
        assert one.hash != two.hash

    def test_it_takes_no_knob_that_restates_the_run_document(self):
        """The no-restatement pin: four parameters, none of them a node value."""
        names = tuple(inspect.signature(serving_document).parameters)
        assert names == ("run_document", "run_dir", "heads", "replay")

    def test_it_derives_without_re_planning_the_run(
        self, training_document, run_dir, monkeypatch
    ):
        """A pure derivation over `document.expanded` (§5.3), not a second plan.

        The run already planned; re-planning at SERVE time would apply
        rules the derivation has just made moot — the winner-consistency
        rule of a search node it is about to drop, most of all.
        """
        monkeypatch.setattr(planner_mod, "plan", boom)
        assert derived(training_document, run_dir).expanded


# --------------------------------------------------------------------------
# search winners
# --------------------------------------------------------------------------


def searched_document(training_document):
    """The training document plus a search node that tuned `grid.offset_ms`."""
    return variant(
        training_document,
        changes={
            "tune": NodeSpec(
                uses="hpo-grid",
                params={
                    "objective": f"${TRAINABLE_NODE}.metrics.n_rows",
                    "direction": "max",
                    "space": {"grid.offset_ms": [0, 1]},
                },
            )
        },
    )


class TestSearchWinners:
    def winner_run(self, tmp_path, doc, winner):
        records = {} if winner is None else {"tune": {"winner": winner}}
        return fake_run_dir(tmp_path, doc, records=records, artifacts=[TRAINABLE_NODE])

    def test_the_recorded_winner_is_applied_to_the_tuned_node(
        self, training_document, tmp_path
    ):
        doc = searched_document(training_document)
        run = self.winner_run(tmp_path, doc, {"grid.offset_ms": 1})
        out = serving_document(doc, run, [HEAD], NO_REPLAY)
        assert out.expanded["grid"].params["offset_ms"] == 1

    def test_the_search_node_is_dropped(self, training_document, tmp_path):
        doc = searched_document(training_document)
        run = self.winner_run(tmp_path, doc, {"grid.offset_ms": 1})
        assert "tune" not in serving_document(doc, run, [HEAD], NO_REPLAY).expanded

    def test_the_winner_goes_through_the_drivers_own_override_rule(
        self, training_document, tmp_path, monkeypatch, recorder
    ):
        doc = searched_document(training_document)
        run = self.winner_run(tmp_path, doc, {"grid.offset_ms": 1})
        real = driver_mod.apply_param_override
        monkeypatch.setattr(
            driver_mod, "apply_param_override", recorder.hook("override", real)
        )
        serving_document(doc, run, [HEAD], NO_REPLAY)
        applied = [args for args, _ in recorder.named("override")]
        assert [(a[1], a[2], a[3]) for a in applied] == [("grid", ("offset_ms",), 1)]

    def test_a_winner_addressing_a_param_that_does_not_exist_refuses(
        self, training_document, tmp_path
    ):
        doc = searched_document(training_document)
        run = self.winner_run(tmp_path, doc, {"grid.bucket_ms": 1})
        with pytest.raises((ProductionError, ValueError)) as exc:
            serving_document(doc, run, [HEAD], NO_REPLAY)
        assert "bucket_ms" in str(exc.value)

    def test_a_needed_search_node_with_no_recorded_winner_refuses(
        self, training_document, tmp_path
    ):
        doc = searched_document(training_document)
        run = self.winner_run(tmp_path, doc, None)
        with pytest.raises(ProductionError) as exc:
            serving_document(doc, run, [HEAD], NO_REPLAY)
        assert "tune" in str(exc.value)

    def test_the_winner_moves_the_derived_hash(self, training_document, tmp_path):
        doc = searched_document(training_document)
        zero = serving_document(
            doc, self.winner_run(tmp_path / "a", doc, {"grid.offset_ms": 0}),
            [HEAD], NO_REPLAY,
        )
        one = serving_document(
            doc, self.winner_run(tmp_path / "b", doc, {"grid.offset_ms": 1}),
            [HEAD], NO_REPLAY,
        )
        assert zero.hash != one.hash


# --------------------------------------------------------------------------
# replayed gate / stat_test nodes
# --------------------------------------------------------------------------


class TestReplayedVerdicts:
    def gated_run(self, tmp_path, doc, carry=None, records=None):
        return fake_run_dir(
            tmp_path, doc,
            records=records if records is not None else {},
            carry={"family": dict(GATE_OUTPUTS)} if carry is None else carry,
            artifacts=[TRAINABLE_NODE],
        )

    def test_a_replayed_gate_becomes_a_recorded_outputs_node(
        self, training_document, tmp_path
    ):
        doc = gated_document(training_document)
        run = self.gated_run(tmp_path, doc)
        out = serving_document(doc, run, [HEAD], {"gate": "recorded"})
        spec = out.expanded["family"]
        assert spec.uses.endswith(":RecordedOutputs")
        assert spec.params["outputs"] == GATE_OUTPUTS
        assert spec.inputs == {}

    def test_a_gate_whose_role_is_not_replayed_is_left_alone(
        self, training_document, tmp_path
    ):
        doc = gated_document(training_document)
        run = self.gated_run(tmp_path, doc)
        out = serving_document(doc, run, [HEAD], NO_REPLAY)
        assert out.expanded["family"].uses == "eligibility"

    def test_a_summarised_record_refuses_rather_than_recomputing(
        self, training_document, tmp_path
    ):
        """A spent stream cannot be replayed — and must never be recomputed live."""
        doc = gated_document(training_document)
        run = self.gated_run(
            tmp_path, doc, carry={},
            records={"family": {"outputs": {"instruments": {"type": "list", "len": 2},
                                            "verdict": "GO"}}},
        )
        with pytest.raises(ProductionError) as exc:
            serving_document(doc, run, [HEAD], {"gate": "recorded"})
        assert "family" in str(exc.value)

    def test_an_absent_record_refuses(self, training_document, tmp_path):
        doc = gated_document(training_document)
        run = self.gated_run(tmp_path, doc, carry={})
        with pytest.raises(ProductionError) as exc:
            serving_document(doc, run, [HEAD], {"gate": "recorded"})
        assert "family" in str(exc.value)

    def test_an_unknown_replay_value_refuses(self, training_document, tmp_path):
        doc = gated_document(training_document)
        run = self.gated_run(tmp_path, doc)
        with pytest.raises(ProductionError):
            serving_document(doc, run, [HEAD], {"gate": "recompute"})

    def test_replaying_a_role_no_node_carries_is_harmless(
        self, training_document, run_dir
    ):
        out = derived(training_document, run_dir, replay={"gate": "recorded"})
        assert set(out.expanded) == set(training_document.expanded)


class TestRecordedOutputsNode:
    def test_it_satisfies_the_gate_roles_planner_rules(self):
        assert issubclass(RecordedOutputs, Node)
        assert RecordedOutputs.role == "gate"
        # Undeclared at CLASS level, so the planner accepts whatever wire
        # the replaced node fed; the INSTANCE declares the recorded names.
        assert RecordedOutputs.outputs is None

    def test_the_stat_test_sibling_carries_that_role(self):
        assert issubclass(RecordedStatTest, RecordedOutputs)
        assert RecordedStatTest.role == "stat_test"

    def test_the_instance_declares_exactly_the_recorded_output_names(self):
        node = RecordedOutputs("family", {"outputs": dict(GATE_OUTPUTS)})
        assert set(node.outputs) == set(GATE_OUTPUTS)

    def test_it_emits_the_recorded_outputs_verbatim(self):
        node = RecordedOutputs("family", {"outputs": dict(GATE_OUTPUTS)})
        assert node.run(None, {}) == GATE_OUTPUTS

    def test_its_serving_effect_is_pure(self):
        assert RecordedOutputs.serving_effect({"outputs": {}}, {}) == "pure"
        assert RecordedStatTest.serving_effect({"outputs": {}}, {}) == "pure"

    def test_an_unknown_param_refuses(self):
        assert RecordedOutputs.validate_params({"outputs": {}, "node": "family"})

    def test_a_summarised_recorded_value_refuses_at_validation(self):
        problems = RecordedOutputs.validate_params(
            {"outputs": {"instruments": {"type": "list", "len": 2}}}
        )
        assert problems


# --------------------------------------------------------------------------
# ServingExecutionPolicy
# --------------------------------------------------------------------------


class Pure(Node):
    """A stand-in class whose serving effect is recorded, never inferred."""

    role = "transform"
    outputs = ("x",)
    seen = []

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        Pure.seen.append((dict(params), dict(verified_run_evidence)))
        return "pure"

    def run(self, ctx, inputs):
        return {"x": 1}


class TestServingExecutionPolicy:
    def policy(self, release_manifest, run_dir, entry=ENTRY_NODE):
        return ServingExecutionPolicy(entry, release=release_manifest, root=run_dir)

    def test_it_is_a_pipeline_execution_policy(self, release_manifest, run_dir):
        assert isinstance(self.policy(release_manifest, run_dir), ExecutionPolicy)

    def test_classify_delegates_to_the_classs_own_hook(self, release_manifest, run_dir):
        Pure.seen = []
        policy = self.policy(release_manifest, run_dir)
        answer = policy.classify("n", Pure, {"a": 1}, {"mode": "load"})
        assert answer == "pure"
        assert Pure.seen == [({"a": 1}, {"mode": "load"})]

    def test_every_answer_is_in_the_closed_vocabulary(self, release_manifest, run_dir):
        policy = self.policy(release_manifest, run_dir)
        assert policy.classify("n", Node, {}, {}) in SERVING_EFFECTS
        assert policy.classify("n", Node, {}, {}) == "forbidden"

    def test_only_the_entry_is_deferred(self, release_manifest, run_dir, training_document):
        policy = self.policy(release_manifest, run_dir)
        assert policy.defer(ENTRY_NODE) is True
        for key in training_document.expanded:
            if key != ENTRY_NODE:
                assert policy.defer(key) is False

    def test_a_pure_node_gets_no_reader(self, release_manifest, run_dir):
        policy = self.policy(release_manifest, run_dir)
        policy.classify(BASE_PASS_NODE, SideTable, {"table": {}}, {})
        assert policy.reader(BASE_PASS_NODE) is None

    def test_a_release_read_node_gets_a_reader_over_its_own_artifact(
        self, release_manifest, run_dir, training_document
    ):
        from dskit.pipeline.fitted import Standardize

        policy = self.policy(release_manifest, run_dir)
        params = training_document.expanded[TRAINABLE_NODE].params
        assert policy.classify(
            TRAINABLE_NODE, Standardize, params,
            {"mode": "load", "artifact_pinned": True},
        ) == "release_read"
        reader = policy.reader(TRAINABLE_NODE)
        assert reader is not None
        assert reader.names() == (SIDECAR,)
        on_disk = open(
            os.path.join(run_dir, "artifacts", TRAINABLE_NODE, SIDECAR), "rb"
        ).read()
        assert reader.get(SIDECAR) in (on_disk, on_disk.decode("utf-8"))

    def test_the_reader_refuses_a_name_outside_the_manifest(
        self, release_manifest, run_dir, training_document
    ):
        from dskit.pipeline.fitted import Standardize

        policy = self.policy(release_manifest, run_dir)
        policy.classify(
            TRAINABLE_NODE, Standardize,
            training_document.expanded[TRAINABLE_NODE].params,
            {"mode": "load", "artifact_pinned": True},
        )
        with pytest.raises(ProductionError):
            policy.reader(TRAINABLE_NODE).get("weights.bin")

    def test_the_reader_refuses_an_artifact_that_no_longer_matches_its_digest(
        self, release_manifest, tmp_path, training_document, run_dir
    ):
        from dskit.pipeline.fitted import Standardize

        tampered = tmp_path / "tampered"
        (tampered / "artifacts" / TRAINABLE_NODE).mkdir(parents=True)
        (tampered / "artifacts" / TRAINABLE_NODE / SIDECAR).write_text("{}", encoding="utf-8")
        policy = ServingExecutionPolicy(
            ENTRY_NODE, release=release_manifest, root=str(tampered)
        )
        policy.classify(
            TRAINABLE_NODE, Standardize,
            training_document.expanded[TRAINABLE_NODE].params,
            {"mode": "load", "artifact_pinned": True},
        )
        with pytest.raises(ProductionError):
            policy.reader(TRAINABLE_NODE).get(SIDECAR)


# --------------------------------------------------------------------------
# Decider.prepare
# --------------------------------------------------------------------------


def a_decider(serve_document, release_manifest, clock, proposer=None):
    """The Decider under test, over the conftest run and release."""
    return Decider(
        serve_document,
        release_manifest,
        registry=None,
        adapter=None,
        proposer=proposer if proposer is not None else IntentRows(intent_params()),
        clock=clock,
    )


def intent_params(**over):
    """`intent-rows` params over the conftest head's own field names."""
    params = {
        "output": "records",
        "fields": {
            "instrument": "instrument",
            "side": "side",
            "qty": "qty",
            "confidence": "confidence",
            "prediction": "prediction",
        },
    }
    params.update(over)
    return params


def prepared(serve_document, release_manifest, clock, run_dir, **kwargs):
    """A Decider that has already planned the serving document."""
    decider = a_decider(serve_document, release_manifest, clock, **kwargs)
    decider.prepare(ASOF, run_dir)
    return decider


class TestPrepare:
    def test_it_classifies_every_needed_node(
        self, serve_document, release_manifest, clock, run_dir
    ):
        effects = a_decider(serve_document, release_manifest, clock).prepare(ASOF, run_dir)
        assert effects == {
            ENTRY_NODE: "entry_read",
            BASE_PASS_NODE: "pure",
            "usable": "pure",
            "grid": "pure",
            TRAINABLE_NODE: "release_read",
            "scored": "pure",
            HEAD: "pure",
        }

    def test_it_publishes_the_derived_documents_hash(
        self, serve_document, release_manifest, clock, run_dir, training_document
    ):
        decider = prepared(serve_document, release_manifest, clock, run_dir)
        assert decider.serving_hash == derived(training_document, run_dir).hash

    def test_it_re_verifies_the_release(
        self, serve_document, release_manifest, clock, tmp_path, run_dir
    ):
        """A drifted artifact refuses at prepare, before anything is planned."""
        copy_dir = tmp_path / "drifted"
        os.makedirs(copy_dir / "artifacts" / TRAINABLE_NODE)
        (copy_dir / "artifacts" / TRAINABLE_NODE / SIDECAR).write_text("{}", encoding="utf-8")
        (copy_dir / "config.json").write_text(
            open(os.path.join(run_dir, "config.json"), encoding="utf-8").read(),
            encoding="utf-8",
        )
        decider = a_decider(serve_document, release_manifest, clock)
        with pytest.raises(ProductionError):
            decider.prepare(ASOF, str(copy_dir))

    def test_it_never_constructs_the_entry(
        self, serve_document, release_manifest, clock, run_dir, monkeypatch
    ):
        """No constructor, no fingerprint, no data_edge before the fetch gate."""
        monkeypatch.setattr(ObservationRows, "__init__", boom)
        a_decider(serve_document, release_manifest, clock).prepare(ASOF, run_dir)

    def test_it_never_scans_the_store(
        self, serve_document, release_manifest, clock, run_dir, monkeypatch
    ):
        monkeypatch.setattr(observations_mod, "scan_stream", boom)
        a_decider(serve_document, release_manifest, clock).prepare(ASOF, run_dir)

    def test_it_never_materialises_splits(
        self, serve_document, release_manifest, clock, run_dir, monkeypatch
    ):
        """Serving neither fits nor scores a split, so none is ever cut."""
        assert json.loads(
            open(os.path.join(run_dir, "config.json"), encoding="utf-8").read()
        )["splits"] is not None
        frames = []
        real = SideTable.run
        monkeypatch.setattr(
            SideTable, "run",
            lambda self, ctx, inputs: (frames.append(ctx), real(self, ctx, inputs))[1],
        )
        prepared(serve_document, release_manifest, clock, run_dir)
        assert frames and all(f.splits is None and not f.splits_info for f in frames)

    def test_the_base_pass_runs_the_non_descendants_once(
        self, serve_document, release_manifest, clock, run_dir, monkeypatch
    ):
        calls = []
        real = SideTable.run
        monkeypatch.setattr(
            SideTable, "run", lambda self, ctx, inputs: (calls.append(1), real(self, ctx, inputs))[1]
        )
        decider = prepared(serve_document, release_manifest, clock, run_dir)
        assert calls == [1]
        batch = decider.read_entry(LAST_ROW_MS)
        decider.evaluate(batch)
        decider.evaluate(batch)
        assert calls == [1]

    def test_the_base_pass_never_runs_a_descendant_of_the_entry(
        self, serve_document, release_manifest, clock, run_dir, monkeypatch
    ):
        from dskit.pipeline.kinds_flow import Filter

        monkeypatch.setattr(Filter, "run", boom)
        a_decider(serve_document, release_manifest, clock).prepare(ASOF, run_dir)


class TestPrepareRefusals:
    def refuses(self, document, release_manifest, clock, run_dir, proposer=None):
        decider = a_decider(document, release_manifest, clock, proposer=proposer)
        with pytest.raises(ProductionError) as exc:
            decider.prepare(ASOF, run_dir)
        return str(exc.value)

    def reserved(self, serve_document, **serving):
        """The conftest serve document with `serving` keys replaced."""
        obj = serve_document.to_obj()
        obj["serving"].update(serving)
        return type(serve_document).from_obj(obj)

    def test_a_second_entry_read_refuses(
        self, serve_document, release_manifest, clock, training_document, tmp_path,
    ):
        doc = variant(
            training_document,
            changes={
                "shadow_bars": dataclasses.replace(
                    training_document.pipeline[ENTRY_NODE]
                ),
                HEAD: dataclasses.replace(
                    training_document.pipeline[HEAD],
                    inputs={"records": "$scored.records",
                            "weight": "$shadow_bars.records"},
                ),
            },
        )
        run = fake_run_dir(tmp_path, doc, artifacts=[TRAINABLE_NODE])
        assert "shadow_bars" in self.refuses(
            self.reserved(serve_document, run_dir=run), release_manifest, clock, run
        )

    def test_no_entry_read_refuses(
        self, serve_document, release_manifest, clock, training_document, tmp_path
    ):
        doc = variant(training_document, drop=[ENTRY_NODE, "usable", "grid",
                                               TRAINABLE_NODE, "scored", HEAD])
        run = fake_run_dir(tmp_path, doc)
        text = self.refuses(
            self.reserved(serve_document, run_dir=run, heads=[BASE_PASS_NODE]),
            release_manifest, clock, run,
        )
        assert ENTRY_NODE in text

    def test_an_entry_that_is_not_the_documents_refuses(
        self, serve_document, release_manifest, clock, run_dir
    ):
        doc = self.reserved(
            serve_document,
            entry={"node": "grid", "param": "period_ms", "window_ms": WINDOW_MS},
        )
        assert "grid" in self.refuses(doc, release_manifest, clock, run_dir)

    def test_a_head_that_does_not_descend_from_the_entry_refuses(
        self, serve_document, release_manifest, clock, run_dir
    ):
        doc = self.reserved(serve_document, heads=[BASE_PASS_NODE])
        assert BASE_PASS_NODE in self.refuses(doc, release_manifest, clock, run_dir)

    def test_a_needed_forbidden_node_refuses(
        self, serve_document, release_manifest, clock, training_document, tmp_path
    ):
        doc = gated_document(training_document)
        run = fake_run_dir(tmp_path, doc, artifacts=[TRAINABLE_NODE],
                           carry={"family": dict(GATE_OUTPUTS)})
        text = self.refuses(
            self.reserved(serve_document, run_dir=run), release_manifest, clock, run
        )
        assert "family" in text and "forbidden" in text

    def test_a_release_read_outside_the_manifest_refuses(
        self, serve_document, clock, run_dir, source_config_hash, feed_spec_obj, tmp_path
    ):
        from tests.production.conftest import build_release_manifest

        empty = build_release_manifest(run_dir, source_config_hash, feed_spec_obj,
                                       artifacts={})
        assert TRAINABLE_NODE in self.refuses(serve_document, empty, clock, run_dir)

    def test_a_window_param_absent_from_the_run_document_refuses(
        self, serve_document, release_manifest, clock, run_dir
    ):
        doc = self.reserved(
            serve_document,
            entry={"node": ENTRY_NODE, "param": "until_ms", "window_ms": WINDOW_MS},
        )
        assert "until_ms" in self.refuses(doc, release_manifest, clock, run_dir)

    def test_a_window_param_the_document_never_declared_refuses(
        self, serve_document, release_manifest, clock, run_dir
    ):
        """`ts_unit` IS in the entry class's _PARAMS — and is not declared.

        The driver's override rule may only address an EXISTING param, so
        an undeclared knob is refused at plan, never created at serve.
        """
        assert "ts_unit" in ObservationRows._PARAMS
        doc = self.reserved(
            serve_document,
            entry={"node": ENTRY_NODE, "param": "ts_unit", "window_ms": WINDOW_MS},
        )
        assert "ts_unit" in self.refuses(doc, release_manifest, clock, run_dir)


# --------------------------------------------------------------------------
# read_entry / evaluate — the one mutable read
# --------------------------------------------------------------------------


class TestReadEntry:
    def test_it_returns_an_entry_batch(
        self, serve_document, release_manifest, clock, run_dir
    ):
        decider = prepared(serve_document, release_manifest, clock, run_dir)
        assert isinstance(decider.read_entry(LAST_ROW_MS), EntryBatch)

    def test_it_applies_exactly_one_window_override(
        self, serve_document, release_manifest, clock, run_dir, monkeypatch, recorder
    ):
        decider = prepared(serve_document, release_manifest, clock, run_dir)
        real = driver_mod.apply_param_override
        monkeypatch.setattr(
            driver_mod, "apply_param_override", recorder.hook("override", real)
        )
        decider.read_entry(LAST_ROW_MS)
        applied = [args for args, _ in recorder.named("override")]
        assert [(a[1], a[2], a[3]) for a in applied] == [
            (ENTRY_NODE, (ENTRY_PARAM,), LAST_ROW_MS - WINDOW_MS)
        ]

    def test_the_window_actually_bounds_the_rows(
        self, serve_document, release_manifest, clock, run_dir
    ):
        decider = prepared(serve_document, release_manifest, clock, run_dir)
        batch = decider.read_entry(LAST_ROW_MS)
        stamps = [r["asof_ms"] for r in batch.outputs["records"]]
        assert stamps and min(stamps) >= LAST_ROW_MS - WINDOW_MS

    def test_it_runs_only_the_entry(
        self, serve_document, release_manifest, clock, run_dir, monkeypatch
    ):
        from dskit.pipeline.kinds_flow import Derive, Filter

        decider = prepared(serve_document, release_manifest, clock, run_dir)
        monkeypatch.setattr(Filter, "run", boom)
        monkeypatch.setattr(Derive, "run", boom)
        monkeypatch.setattr(SideTable, "run", boom)
        decider.read_entry(LAST_ROW_MS)

    def test_the_batch_covers_the_declared_universe(
        self, serve_document, release_manifest, clock, run_dir
    ):
        decider = prepared(serve_document, release_manifest, clock, run_dir)
        batch = decider.read_entry(LAST_ROW_MS)
        assert set(batch.watermarks_by_key) == set(UNIVERSE)
        assert batch.data_asof_ms == min(
            w.latest_asof_ms for w in batch.watermarks_by_key.values()
        )

    def test_the_batch_binds_the_releases_source_config_hash(
        self, serve_document, release_manifest, clock, run_dir
    ):
        decider = prepared(serve_document, release_manifest, clock, run_dir)
        batch = decider.read_entry(LAST_ROW_MS)
        assert batch.source_config_hash == release_manifest.source_config["hash"]

    def test_two_reads_of_the_same_tick_agree(
        self, serve_document, release_manifest, clock, run_dir
    ):
        decider = prepared(serve_document, release_manifest, clock, run_dir)
        assert decider.read_entry(LAST_ROW_MS) == decider.read_entry(LAST_ROW_MS)


class TestEvaluate:
    def test_it_returns_the_head_outputs_and_their_digest(
        self, serve_document, release_manifest, clock, run_dir
    ):
        decider = prepared(serve_document, release_manifest, clock, run_dir)
        head_outputs, head_digest = decider.evaluate(decider.read_entry(LAST_ROW_MS))
        assert set(head_outputs) == {HEAD}
        assert head_digest == canonical_hash(head_outputs)

    def test_the_head_rows_are_proposal_shaped(
        self, serve_document, release_manifest, clock, run_dir
    ):
        decider = prepared(serve_document, release_manifest, clock, run_dir)
        head_outputs, _ = decider.evaluate(decider.read_entry(LAST_ROW_MS))
        row = head_outputs[HEAD]["records"][0]
        assert {"instrument", "side", "qty", "confidence", "prediction"} <= set(row)

    def test_the_entry_is_not_re_run(
        self, serve_document, release_manifest, clock, run_dir, monkeypatch
    ):
        """No second mutable snapshot can occur: the policy DEFERS the entry."""
        decider = prepared(serve_document, release_manifest, clock, run_dir)
        batch = decider.read_entry(LAST_ROW_MS)
        monkeypatch.setattr(observations_mod, "scan_stream", boom)
        monkeypatch.setattr(ObservationRows, "run", boom)
        decider.evaluate(batch)

    def test_it_evaluates_the_batch_it_was_given(
        self, serve_document, release_manifest, clock, run_dir
    ):
        decider = prepared(serve_document, release_manifest, clock, run_dir)
        wide = decider.read_entry(LAST_ROW_MS)
        narrow = decider.read_entry(LAST_ROW_MS - DAY_MS)
        assert decider.evaluate(wide)[1] != decider.evaluate(narrow)[1]

    def test_the_same_batch_evaluates_to_the_same_digest(
        self, serve_document, release_manifest, clock, run_dir
    ):
        decider = prepared(serve_document, release_manifest, clock, run_dir)
        batch = decider.read_entry(LAST_ROW_MS)
        assert decider.evaluate(batch)[1] == decider.evaluate(batch)[1]

    def test_a_batch_from_another_release_refuses(
        self, serve_document, release_manifest, clock, run_dir
    ):
        decider = prepared(serve_document, release_manifest, clock, run_dir)
        batch = decider.read_entry(LAST_ROW_MS)
        foreign = dataclasses.replace(batch, source_config_hash="f" * 64)
        with pytest.raises(ProductionError):
            decider.evaluate(foreign)

    def test_reading_before_preparing_refuses(
        self, serve_document, release_manifest, clock
    ):
        decider = a_decider(serve_document, release_manifest, clock)
        with pytest.raises(ProductionError):
            decider.read_entry(LAST_ROW_MS)


# --------------------------------------------------------------------------
# Proposer, IntentRows, TargetPositions
# --------------------------------------------------------------------------


def a_provenance(batch=None):
    """The frozen provenance a tick hands its proposer."""
    return Provenance(
        inputs_asof_ms=LAST_ROW_MS if batch is None else batch.data_asof_ms,
        inputs_digest="a" * 64 if batch is None else batch.inputs_digest,
        coverage_digest="b" * 64 if batch is None else batch.coverage_digest,
        quote_asof_ms=LAST_ROW_MS,
        quote_digest="c" * 64,
    )


def an_account(positions=()):
    """A minimal `AccountState` — only `positions` matters to a proposer."""
    return AccountState(
        risk_version=RiskVersion(economic_seq=1, executor_token=None, accounting_tokens=None),
        asof_ms=NOW_MS,
        evidence_digest="d" * 64,
        balances=(),
        positions=tuple(positions),
        working=(),
        measure_evidence={},
        source_digests={},
    )


def head_rows(*rows):
    """Head outputs carrying `rows` under the conftest proposer's port."""
    return {HEAD: {"records": list(rows)}}


def a_row(**over):
    """One proposal-shaped head row."""
    row = {
        "instrument": UNIVERSE[0], "side": "buy", "qty": 3,
        "confidence": 0.61, "prediction": 0.58, "asof_ms": LAST_ROW_MS,
    }
    row.update(over)
    return row


class TestProposerSeam:
    def test_candidates_and_proposals_are_the_abstract_hooks(self):
        assert Proposer.__abstractmethods__ == frozenset({"candidates", "proposals"})

    def test_quotes_is_concrete(self):
        assert "quotes" not in Proposer.__abstractmethods__

    def test_candidates_cannot_see_account_state(self):
        """Candidate/scope derivation is release-bound and state-independent."""
        assert tuple(inspect.signature(Proposer.candidates).parameters) == (
            "self", "head_outputs",
        )
        assert tuple(inspect.signature(Proposer.quotes).parameters) == (
            "self", "head_outputs",
        )

    def test_proposals_takes_the_frozen_provenance(self):
        assert tuple(inspect.signature(Proposer.proposals).parameters) == (
            "self", "head_outputs", "candidates", "state", "provenance",
        )

    def test_the_registry_carries_exactly_the_two_core_kinds(self):
        assert PROPOSER_KINDS.kinds() == ("intent-rows", "target-positions")
        assert PROPOSER_KINDS.resolve("intent-rows") is IntentRows
        assert PROPOSER_KINDS.resolve("target-positions") is TargetPositions

    def test_the_default_quotes_reads_market_record_shaped_rows(self):
        rows = [
            MarketRecord(
                venue="synthetic", instrument=UNIVERSE[0], contract=UNIVERSE[0],
                asof_ms=LAST_ROW_MS, usable=True, reason="ok", group=None,
                bid=0.40, ask=0.42, mid=0.41, lead_frac=None, native=None,
            )
        ]
        quotes = IntentRows(intent_params()).quotes(head_rows(*rows))
        assert [type(q) for q in quotes] == [Quote]
        assert quotes[0].instrument == UNIVERSE[0]
        assert quotes[0].mid == Decimal("0.41")
        assert quotes[0].asof_ms == LAST_ROW_MS

    def test_quotes_never_depend_on_state(self):
        rows = [
            MarketRecord(
                venue="synthetic", instrument=key, contract=key, asof_ms=LAST_ROW_MS,
                usable=True, reason="ok", group=None, bid=0.4, ask=0.42, mid=0.41,
                lead_frac=None, native=None,
            )
            for key in UNIVERSE
        ]
        proposer = IntentRows(intent_params())
        first = proposer.quotes(head_rows(*rows))
        second = proposer.quotes(head_rows(*rows))
        assert first == second


class TestIntentRows:
    def test_its_params_are_default_deny(self):
        assert IntentRows.validate_params({**intent_params(), "tif": "ioc"})

    def test_it_reads_the_declared_output_port(self):
        proposer = IntentRows(intent_params())
        candidates = proposer.candidates(head_rows(a_row(), a_row(instrument=UNIVERSE[1])))
        assert [c.instrument for c in candidates] == list(UNIVERSE)
        assert all(isinstance(c, Candidate) for c in candidates)

    def test_an_output_port_no_head_carries_refuses(self):
        proposer = IntentRows(intent_params(output="nope"))
        with pytest.raises(ProductionError):
            proposer.candidates(head_rows(a_row()))

    def test_each_candidates_scope_keys_default_to_its_instrument(self):
        proposer = IntentRows(intent_params())
        candidate = proposer.candidates(head_rows(a_row()))[0]
        assert candidate.scope_keys == (UNIVERSE[0],)

    def test_candidate_ids_are_stable_across_calls(self):
        proposer = IntentRows(intent_params())
        rows = head_rows(a_row(), a_row(instrument=UNIVERSE[1]))
        assert [c.id for c in proposer.candidates(rows)] == [
            c.id for c in proposer.candidates(rows)
        ]

    def test_duplicate_candidate_ids_refuse(self):
        proposer = IntentRows(intent_params())
        with pytest.raises(ProductionError):
            proposer.candidates(head_rows(a_row(), a_row()))

    def test_proposals_preserve_each_candidates_id_and_instrument(self):
        proposer = IntentRows(intent_params())
        rows = head_rows(a_row(), a_row(instrument=UNIVERSE[1]))
        candidates = proposer.candidates(rows)
        proposals = proposer.proposals(rows, candidates, an_account(), a_provenance())
        assert [(p.id, p.instrument) for p in proposals] == [
            (c.id, c.instrument) for c in candidates
        ]
        assert all(isinstance(p, Proposal) for p in proposals)

    def test_proposals_bind_the_provenance_they_were_given(self):
        proposer = IntentRows(intent_params())
        rows = head_rows(a_row())
        provenance = a_provenance()
        proposal = proposer.proposals(
            rows, proposer.candidates(rows), an_account(), provenance
        )[0]
        assert proposal.inputs_digest == provenance.inputs_digest
        assert proposal.coverage_digest == provenance.coverage_digest
        assert proposal.inputs_asof_ms == provenance.inputs_asof_ms
        assert proposal.quote_digest == provenance.quote_digest

    def test_a_proposal_carries_the_mapped_fields(self):
        proposer = IntentRows(intent_params())
        rows = head_rows(a_row(side="sell", qty=7, confidence=0.9, prediction=0.2))
        proposal = proposer.proposals(
            rows, proposer.candidates(rows), an_account(), a_provenance()
        )[0]
        assert proposal.side == "sell"
        assert proposal.qty == Decimal("7")
        assert proposal.confidence == 0.9 and proposal.prediction == 0.2

    def test_the_default_tif_is_a_named_knob(self):
        plain = IntentRows(intent_params())
        pinned = IntentRows(intent_params(default_tif="fok"))
        rows = head_rows(a_row())
        first = plain.proposals(rows, plain.candidates(rows), an_account(), a_provenance())
        second = pinned.proposals(rows, pinned.candidates(rows), an_account(), a_provenance())
        assert second[0].tif == "fok"
        assert first[0].tif != "fok"

    def test_a_candidate_whose_scope_keys_changed_refuses(self):
        proposer = IntentRows(intent_params())
        rows = head_rows(a_row())
        candidates = proposer.candidates(rows)
        tampered = [dataclasses.replace(candidates[0], scope_keys=("OTHER",))]
        with pytest.raises(ProductionError):
            proposer.proposals(rows, tampered, an_account(), a_provenance())

    def test_a_candidate_no_row_backs_refuses(self):
        proposer = IntentRows(intent_params())
        rows = head_rows(a_row())
        stray = Candidate(id="ghost", instrument="INS9", scope_keys=("INS9",))
        with pytest.raises(ProductionError):
            proposer.proposals(rows, list(proposer.candidates(rows)) + [stray],
                               an_account(), a_provenance())

    def test_candidates_do_not_move_when_account_state_does(self):
        proposer = IntentRows(intent_params())
        rows = head_rows(a_row(), a_row(instrument=UNIVERSE[1]))
        flat = proposer.candidates(rows)
        held = proposer.candidates(rows)
        assert flat == held
        long_book = an_account(
            positions=[Position(instrument=UNIVERSE[0], qty=Decimal("99"),
                                avg_cost=Decimal("1"), source="derived", native=None)]
        )
        assert proposer.candidates(rows) == flat
        # …and the SIZES are the only thing state may touch.
        one = proposer.proposals(rows, flat, an_account(), a_provenance())
        two = proposer.proposals(rows, flat, long_book, a_provenance())
        assert [(p.id, p.instrument) for p in one] == [(p.id, p.instrument) for p in two]


class TestTargetPositions:
    def proposer(self, **over):
        params = {"output": "records",
                  "fields": {"instrument": "instrument", "qty": "qty"}}
        params.update(over)
        return TargetPositions(params)

    def test_its_params_are_default_deny(self):
        assert TargetPositions.validate_params(
            {"output": "records", "fields": {}, "default_tif": "ioc"}
        )

    def test_a_target_above_the_held_position_buys_the_difference(self):
        proposer = self.proposer()
        rows = head_rows(a_row(qty=10))
        account = an_account(
            positions=[Position(instrument=UNIVERSE[0], qty=Decimal("4"),
                                avg_cost=Decimal("1"), source="derived", native=None)]
        )
        proposal = proposer.proposals(
            rows, proposer.candidates(rows), account, a_provenance()
        )[0]
        assert proposal.side == "buy"
        assert proposal.qty == Decimal("6")

    def test_a_target_below_the_held_position_sells_the_difference(self):
        proposer = self.proposer()
        rows = head_rows(a_row(qty=1))
        account = an_account(
            positions=[Position(instrument=UNIVERSE[0], qty=Decimal("4"),
                                avg_cost=Decimal("1"), source="derived", native=None)]
        )
        proposal = proposer.proposals(
            rows, proposer.candidates(rows), account, a_provenance()
        )[0]
        assert proposal.side == "sell"
        assert proposal.qty == Decimal("3")

    def test_a_target_already_held_abstains(self):
        proposer = self.proposer()
        rows = head_rows(a_row(qty=4))
        account = an_account(
            positions=[Position(instrument=UNIVERSE[0], qty=Decimal("4"),
                                avg_cost=Decimal("1"), source="derived", native=None)]
        )
        proposal = proposer.proposals(
            rows, proposer.candidates(rows), account, a_provenance()
        )[0]
        assert proposal.side == "none"

    def test_its_candidates_are_state_independent(self):
        proposer = self.proposer()
        rows = head_rows(a_row(qty=10), a_row(instrument=UNIVERSE[1], qty=2))
        assert proposer.candidates(rows) == proposer.candidates(rows)


# --------------------------------------------------------------------------
# the whole tick, once
# --------------------------------------------------------------------------


class TestDeciderEndToEnd:
    def test_prepare_read_evaluate_propose(
        self, serve_document, release_manifest, clock, run_dir
    ):
        decider = prepared(serve_document, release_manifest, clock, run_dir)
        batch = decider.read_entry(LAST_ROW_MS)
        head_outputs, head_digest = decider.evaluate(batch)
        proposer = IntentRows(intent_params())
        candidates = proposer.candidates(head_outputs)
        proposals = proposer.proposals(
            head_outputs, candidates, an_account(), a_provenance(batch)
        )
        assert len(head_digest) == 64
        assert {c.instrument for c in candidates} == set(UNIVERSE)
        assert {p.inputs_digest for p in proposals} == {batch.inputs_digest}

    def test_two_ticks_over_the_same_data_agree(
        self, serve_document, release_manifest, clock, run_dir
    ):
        decider = prepared(serve_document, release_manifest, clock, run_dir)
        first = decider.evaluate(decider.read_entry(LAST_ROW_MS))
        clock.advance(60_000)
        second = decider.evaluate(decider.read_entry(LAST_ROW_MS))
        assert first[1] == second[1]

    def test_the_serving_document_is_never_the_run_document(
        self, serve_document, release_manifest, clock, run_dir, training_document
    ):
        decider = prepared(serve_document, release_manifest, clock, run_dir)
        assert decider.serving_hash != training_document.hash
        assert copy.deepcopy(training_document) == training_document
