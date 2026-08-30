"""The driver: the 6-step lifecycle end to end, against synthetic nodes."""

import io
import json
import logging
import os

import pytest

from dskit.pipeline.base import (
    SINK_KINDS,
    ConfigError,
    EnvConfig,
    OutputsConfig,
    SinkConfig,
    TrackingConfig,
    register_sink_kind,
)
from dskit.pipeline.document import (
    ClockConfig,
    NodeSpec,
    PipelineDocument,
    TrailingSplitSpec,
    save_document,
)
from dskit.pipeline.driver import (
    DocumentRunResult,
    _RELEASE_MIN_LEN,
    _carryable,
    _is_summary,
    _node_metrics,
    _summarize,
    _too_big_to_carry,
    run_document,
)
from dskit.pipeline.node import Node, NodeKindRegistry
from dskit.pipeline.testing import MemoryTracker
from tests.pipeline.dochelpers import banking_document, banking_pipeline, make_registry

ASOF = "2026-01-01"


class ReplacingTracker:
    """A Tracker seam implemented the blunt way — each ``log_params`` call
    REPLACES what the sink holds. Legal, because the seam's contract is ONE
    call per run; ``payloads`` keeps every call so a test can count them
    and read what was sent."""

    instances = []

    def __init__(self, params):
        self.params = {}
        self.payloads = []
        ReplacingTracker.instances.append(self)

    def log_params(self, mapping):
        self.payloads.append(dict(mapping))
        self.params = dict(mapping)

    def log_metrics(self, stage, mapping):
        pass

    def close(self):
        pass


class BadContractNode(Node):
    role = "transform"
    outputs = ("x",)

    def run(self, ctx, inputs):
        return {"y": 1}


@pytest.fixture
def registry():
    return make_registry()


def bdoc(tmp_path, **overrides):
    overrides.setdefault("outputs", OutputsConfig(run_root=str(tmp_path)))
    return banking_document(**overrides)


def read_json(run_dir, name):
    with open(os.path.join(run_dir, name), encoding="utf-8") as fh:
        return json.load(fh)


class TestCleanRun:
    def test_end_to_end_banking_run(self, tmp_path, registry):
        result = run_document(bdoc(tmp_path), asof=ASOF, registry=registry)
        assert isinstance(result, DocumentRunResult)
        assert result.state == "ran" and result.exit_code == 0
        assert set(result.node_states.values()) == {"ok"}
        # The planted edge deploys: both instruments survive, capital sizes.
        assert result.outputs["edge_test"]["survivors"] == ["SYNA", "SYNB"]
        assert result.outputs["size"]["positions"]
        assert result.outputs["size"]["final_bankroll"] == pytest.approx(1020.0)

    def test_run_dir_layout_and_naming(self, tmp_path, registry):
        doc = bdoc(tmp_path)
        result = run_document(doc, asof=ASOF, registry=registry)
        base = os.path.basename(result.run_dir)
        assert base == f"synth-banking-{ASOF}-{result.run_hash[:8]}"
        for artifact in (
            "config.json",
            "plan.json",
            "resolved.json",
            "result.json",
            "report.md",
            "carry.json",
            "run.log",
        ):
            assert os.path.isfile(os.path.join(result.run_dir, artifact)), artifact
        assert os.path.isfile(
            os.path.join(result.run_dir, "artifacts", "qhat", "model.json")
        )
        records = sorted(os.listdir(os.path.join(result.run_dir, "nodes")))
        assert len(records) == len(banking_pipeline())
        assert records[0].startswith("01-")

    def test_report_leads_with_the_verdict(self, tmp_path, registry):
        result = run_document(bdoc(tmp_path), asof=ASOF, registry=registry)
        with open(os.path.join(result.run_dir, "report.md"), encoding="utf-8") as fh:
            first = fh.readline().strip()
        assert first.startswith("**RAN")

    def test_result_json_mirrors_the_result(self, tmp_path, registry):
        result = run_document(bdoc(tmp_path), asof=ASOF, registry=registry)
        payload = read_json(result.run_dir, "result.json")
        assert payload["state"] == "ran" and payload["exit_code"] == 0
        assert payload["run_hash"] == result.run_hash
        assert payload["node_states"] == result.node_states

    def test_run_log_narrates_the_nodes(self, tmp_path, registry):
        result = run_document(bdoc(tmp_path), asof=ASOF, registry=registry)
        with open(os.path.join(result.run_dir, "run.log"), encoding="utf-8") as fh:
            log = fh.read()
        assert "node events: start" in log and "node size: ok" in log

    def test_document_loads_from_a_path(self, tmp_path, registry):
        doc_path = tmp_path / "doc.json"
        save_document(bdoc(tmp_path / "runs"), doc_path)
        result = run_document(str(doc_path), asof=ASOF, registry=registry)
        assert result.state == "ran"


class TestHaltSemantics:
    def test_nogo_gate_halts_descendants_only(self, tmp_path, registry):
        pipeline = banking_pipeline()
        pipeline["family"] = NodeSpec(
            uses="synth-eligibility",
            inputs={"counts": "$bank.counts"},
            params={"min_events": 10_000},
        )
        result = run_document(
            bdoc(tmp_path, pipeline=pipeline), asof=ASOF, registry=registry
        )
        assert result.state == "halted" and result.exit_code == 3
        assert result.halted_at == "family"
        assert result.node_states["family"] == "ok"  # the gate itself ran
        assert result.node_states["report"] == "halted"  # downstream of family
        # Independent branches kept running — this is a DAG halt, not a break.
        assert result.node_states["edge_test"] == "ok"
        assert result.node_states["size"] == "ok"

    def test_nogo_stat_test_halts_capital(self, tmp_path, registry):
        pipeline = banking_pipeline()
        pipeline["edge_test"] = NodeSpec(
            uses="stat_test",
            inputs={"scores": "$validate.cluster_scores"},
            params={"alpha": 1e-9},
        )
        result = run_document(
            bdoc(tmp_path, pipeline=pipeline), asof=ASOF, registry=registry
        )
        assert result.state == "halted" and result.halted_at == "edge_test"
        assert result.node_states["size"] == "halted"
        assert result.node_states["report"] == "halted"
        with open(os.path.join(result.run_dir, "report.md"), encoding="utf-8") as fh:
            assert fh.readline().startswith("**NO-GO — halted at `edge_test`")


class TestErrorSemantics:
    def test_node_exception_is_recorded_and_aborts(self, tmp_path, registry):
        pipeline = banking_pipeline()
        pipeline["qhat"] = NodeSpec(
            uses="synth-train",
            mode="train",
            inputs={"events": "$clip.events"},
            params={"min_train": 10_000},
        )
        result = run_document(
            bdoc(tmp_path, pipeline=pipeline), asof=ASOF, registry=registry
        )
        assert result.state == "error" and result.exit_code == 1
        assert result.node_states["qhat"] == "error"
        assert result.node_states["market"] == "ok"  # ran before the failure
        assert result.node_states["size"] == "not_run"  # aborted, not halted
        record = read_json(result.run_dir, os.path.join("nodes", "07-qhat.json"))
        assert record["status"] == "error" and "min_train=10000" in record["error"]
        with open(os.path.join(result.run_dir, "report.md"), encoding="utf-8") as fh:
            assert fh.readline().startswith("**ERROR at `qhat`")

    def test_validate_inputs_problems_fail_the_node(self, tmp_path, registry):
        pipeline = banking_pipeline()
        pipeline["validate"] = NodeSpec(
            uses="synth-score",
            inputs={
                "events": "$clip.events",
                "signal": "$events.instruments",  # a list, not a signal dict
                "baseline": "$market.signal",
                "outcomes": "$labels.outcomes",
            },
            params={"split": "val"},
        )
        result = run_document(
            bdoc(tmp_path, pipeline=pipeline), asof=ASOF, registry=registry
        )
        assert result.state == "error"
        assert result.node_states["validate"] == "error"
        assert "signal must be a dict" in result.error

    def test_output_contract_violation_fails_the_node(self, tmp_path, registry):
        pipeline = {
            "events": NodeSpec(uses="synth-events", params={"n_events": 8}),
            "bad": NodeSpec(
                uses="tests.pipeline.test_driver:BadContractNode",
                inputs={"events": "$events.events"},
            ),
        }
        doc = PipelineDocument(
            name="contract-break",
            pipeline=pipeline,
            outputs=OutputsConfig(run_root=str(tmp_path)),
        )
        result = run_document(doc, asof=ASOF, registry=registry)
        assert result.state == "error"
        assert "undeclared ['y']" in result.error


class TestPrevCarry:
    def test_bankroll_carries_run_over_run(self, tmp_path, registry):
        first = run_document(bdoc(tmp_path), asof="2026-01-01", registry=registry)
        assert first.outputs["size"]["final_bankroll"] == pytest.approx(1020.0)
        assert read_json(first.run_dir, "resolved.json")["prev_bindings"] == {
            "size.final_bankroll": "default"
        }
        second = run_document(bdoc(tmp_path), asof="2026-01-08", registry=registry)
        assert second.prev_run == first.run_dir
        assert second.outputs["size"]["final_bankroll"] == pytest.approx(1040.4)
        resolved = read_json(second.run_dir, "resolved.json")
        assert resolved["prev_bindings"] == {"size.final_bankroll": "prev"}
        assert resolved["prev_run"] == first.run_dir

    def test_missing_prev_output_falls_back_to_default_and_says_so(
        self, tmp_path, registry
    ):
        first = run_document(bdoc(tmp_path), asof="2026-01-01", registry=registry)
        carry = read_json(first.run_dir, "carry.json")
        del carry["size"]
        with open(
            os.path.join(first.run_dir, "carry.json"), "w", encoding="utf-8"
        ) as fh:
            json.dump(carry, fh)
        second = run_document(bdoc(tmp_path), asof="2026-01-08", registry=registry)
        assert second.outputs["size"]["final_bankroll"] == pytest.approx(1020.0)
        assert read_json(second.run_dir, "resolved.json")["prev_bindings"] == {
            "size.final_bankroll": "default"
        }

    def test_carry_holds_state_not_datasets(self, tmp_path, registry):
        result = run_document(bdoc(tmp_path), asof=ASOF, registry=registry)
        carry = read_json(result.run_dir, "carry.json")
        assert carry["size"]["final_bankroll"] == pytest.approx(1020.0)
        assert "events" not in carry.get("events", {})  # the big list is not carried
        assert "instruments" in carry["events"]


class TestRefusals:
    def test_occupied_run_dir_refused(self, tmp_path, registry):
        run_document(bdoc(tmp_path), asof=ASOF, registry=registry)
        with pytest.raises(ValueError, match="already exists"):
            run_document(bdoc(tmp_path), asof=ASOF, registry=registry)

    def test_clock_documents_refuse_to_run(self, tmp_path, registry):
        doc = bdoc(tmp_path, clock=ClockConfig(increment="epoch"))
        with pytest.raises(ConfigError, match="I-222"):
            run_document(doc, asof=ASOF, registry=registry)

    def test_trailing_splits_refuse_to_resolve(self, tmp_path, registry):
        doc = bdoc(tmp_path, splits=TrailingSplitSpec(test_days=14, val_days=28))
        with pytest.raises(ConfigError, match="trailing"):
            run_document(doc, asof=ASOF, registry=registry)

    def test_bad_asof_refused(self, tmp_path, registry):
        with pytest.raises(ConfigError, match="asof"):
            run_document(bdoc(tmp_path), asof="Jan 1", registry=registry)

    def test_missing_required_env_lists_the_names(self, tmp_path, registry):
        doc = bdoc(
            tmp_path,
            env=EnvConfig(
                env_file=str(tmp_path / "none.env"), require=("PMQ_MISSING_XYZ",)
            ),
        )
        with pytest.raises(ValueError, match="PMQ_MISSING_XYZ"):
            run_document(doc, asof=ASOF, registry=registry)
        assert not os.path.isdir(os.path.join(tmp_path, f"synth-banking-{ASOF}"))


class TestTracking:
    def register_memory(self):
        if "memory" not in SINK_KINDS:
            register_sink_kind("memory", lambda params: [], MemoryTracker)

    def test_metrics_and_params_reach_the_sink_and_it_closes(self, tmp_path, registry):
        self.register_memory()
        doc = bdoc(
            tmp_path,
            tracking=TrackingConfig(sinks=(SinkConfig(kind="memory"),)),
        )
        result = run_document(doc, asof=ASOF, registry=registry)
        sink = MemoryTracker.instances[-1]
        assert sink.closed
        assert sink.logged_params["run_hash"] == result.run_hash
        logged = {node: m for node, m in sink.metrics}
        assert "metrics.loss" in logged["validate"]
        assert logged["size"]["final_bankroll"] == pytest.approx(1020.0)

    def test_node_params_reach_the_sink_beside_the_identity_fields(
        self, tmp_path, registry
    ):
        # Identity alone made runs unfilterable: with only name/asof/hashes
        # in the payload you could not ask a sink for "the runs at
        # n_events=432". Every node's params ride along, flattened to the
        # same '<node>.<param.path>' keys hpo-grid tunes.
        self.register_memory()
        doc = bdoc(
            tmp_path,
            tracking=TrackingConfig(sinks=(SinkConfig(kind="memory"),)),
        )
        result = run_document(doc, asof=ASOF, registry=registry)
        logged = MemoryTracker.instances[-1].logged_params
        assert logged["events.n_events"] == 432
        assert logged["clip.lo"] == 0.02
        assert logged["size.stake_frac"] == 0.1
        assert logged["name"] == doc.name
        assert logged["asof"] == ASOF
        assert logged["document_hash"] == doc.hash
        assert logged["run_hash"] == result.run_hash
        assert logged["nodes"].startswith("events,")

    def test_a_prev_carry_logs_as_the_reference_it_was_declared_as(
        self, tmp_path, registry
    ):
        # Round-4 ruling (findings 1+2+3): keys and values follow the
        # DECLARED document, and a reference logs as a reference. Logging
        # the carry RESOLVED would make 'size.bankroll' a different value
        # every run of the series; the declared spec is stable, bounded,
        # and IS the config — what the carry bound to lives in
        # resolved.json and the prior run's outputs, where it happened.
        self.register_memory()
        declared = banking_pipeline()["size"].params["bankroll"]
        assert "$prev" in declared  # the fixture still carries; else vacuous

        def run(asof):
            doc = bdoc(
                tmp_path, tracking=TrackingConfig(sinks=(SinkConfig(kind="memory"),))
            )
            return run_document(doc, asof=asof, registry=registry)

        first = run("2026-01-01")
        first_logged = dict(MemoryTracker.instances[-1].logged_params)
        second = run("2026-01-08")
        second_logged = dict(MemoryTracker.instances[-1].logged_params)
        assert second.prev_run == first.run_dir  # a real series, not two firsts
        assert first_logged["size.bankroll"] == declared
        assert second_logged["size.bankroll"] == declared
        # Descent never enters a reference: the carry contributes no
        # subtree keys, and the payload's KEY SET holds across the series.
        assert "size.bankroll.default" not in first_logged
        assert set(first_logged) == set(second_logged)

    def test_a_dict_valued_carry_also_logs_as_its_declared_reference(
        self, tmp_path, registry
    ):
        # Round-4 ruling (findings 1+2+3): the dict-valued carry was the
        # unstable case — resolving it whole gave run 1 keys from the
        # literal default and run 2 whatever keys the prior run's output
        # happened to hold, so the payload's key set drifted across the
        # series and spelled targets no override or space key can address.
        # The declared reference is ONE stable leaf, both runs alike.
        self.register_memory()
        spec = {"$prev": "size.positions", "default": {"lr": 0.1}}

        def run(asof):
            pipeline = banking_pipeline()
            pipeline["size"] = NodeSpec(
                uses="synth-capital",
                inputs=dict(pipeline["size"].inputs),
                params={**pipeline["size"].params, "cfg": dict(spec)},
            )
            doc = bdoc(
                tmp_path,
                pipeline=pipeline,
                tracking=TrackingConfig(sinks=(SinkConfig(kind="memory"),)),
            )
            return run_document(doc, asof=asof, registry=registry)

        first = run("2026-01-01")
        first_logged = dict(MemoryTracker.instances[-1].logged_params)
        assert first.state == "ran"
        second = run("2026-01-08")
        second_logged = dict(MemoryTracker.instances[-1].logged_params)
        assert second.prev_run == first.run_dir
        assert first_logged["size.cfg"] == spec
        assert second_logged["size.cfg"] == spec
        assert "size.cfg.lr" not in first_logged  # descent never enters a ref
        assert set(first_logged) == set(second_logged)

    def test_a_node_whose_entire_params_block_is_a_carry_logs_no_keys(
        self, tmp_path, registry
    ):
        # Round-5 ruling (finding 1, refining 1+2+3): a root-level carry
        # is pure wiring — the node declares no addressable knob, and the
        # params block has no path of its own to be emitted under — so it
        # contributes NOTHING. Descending it logged the carry's 'default'
        # plumbing as knobs ('size.default.*') whose values contradict
        # every run after the first.
        self.register_memory()

        def run(asof):
            pipeline = banking_pipeline()
            pipeline["size"] = NodeSpec(
                uses="synth-capital",
                inputs=dict(pipeline["size"].inputs),
                params={
                    "$prev": "size.no_such_output",  # misses -> default binds
                    "default": {"bankroll": 1000.0, "stake_frac": 0.1},
                },
            )
            doc = bdoc(
                tmp_path,
                pipeline=pipeline,
                tracking=TrackingConfig(sinks=(SinkConfig(kind="memory"),)),
            )
            return run_document(doc, asof=asof, registry=registry)

        first = run("2026-01-01")
        first_logged = dict(MemoryTracker.instances[-1].logged_params)
        second = run("2026-01-08")
        second_logged = dict(MemoryTracker.instances[-1].logged_params)
        assert first.state == "ran" and second.state == "ran"
        assert second.prev_run == first.run_dir  # a real series
        assert not [k for k in first_logged if k.startswith("size.")]
        assert not [k for k in second_logged if k.startswith("size.")]

    def test_a_param_wired_to_a_node_output_logs_the_REFERENCE(
        self, tmp_path, registry
    ):
        # A param declared as '$node.port' is WIRING, not a hyperparameter:
        # its resolved value is another node's output — already recorded as
        # that node's output, possibly a whole dataset, and meaningless as a
        # sink filter. The declaration is what identifies the config, and it
        # is bounded, so it is what gets logged.
        self.register_memory()
        pipeline = banking_pipeline()
        pipeline["clip"] = NodeSpec(
            uses="synth-clip",
            inputs={"events": "$events.events"},
            params={"lo": 0.02, "hi": 0.98, "note": "$events.instruments"},
        )
        doc = bdoc(
            tmp_path,
            pipeline=pipeline,
            tracking=TrackingConfig(sinks=(SinkConfig(kind="memory"),)),
        )
        result = run_document(doc, asof=ASOF, registry=registry)
        logged = MemoryTracker.instances[-1].logged_params
        assert result.state == "ran"
        assert logged["clip.note"] == "$events.instruments"
        assert logged["clip.lo"] == 0.02  # ordinary knobs still log their value

    def test_log_params_is_called_once_with_identity_and_hyperparameters(
        self, tmp_path, registry
    ):
        # Round-4 ruling (finding 5): the Tracker contract is ONE
        # log_params per run, at run start — the five identity fields and
        # the flattened declared params in a single payload. A sink that
        # REPLACES on each call (the blunt reading of the seam) therefore
        # cannot lose a field, and an mlflow-style sink that refuses to
        # restate a param is never asked to.
        doc = bdoc(
            tmp_path,
            tracking=TrackingConfig(
                sinks=(SinkConfig(kind="tests.pipeline.test_driver:ReplacingTracker"),)
            ),
        )
        result = run_document(doc, asof=ASOF, registry=registry)
        sink = ReplacingTracker.instances[-1]
        assert len(sink.payloads) == 1
        payload = sink.payloads[0]
        assert payload["name"] == doc.name
        assert payload["asof"] == ASOF
        assert payload["document_hash"] == doc.hash
        assert payload["run_hash"] == result.run_hash
        assert payload["nodes"].startswith("events,")
        assert payload["events.n_events"] == 432  # knobs ride the same call

    def test_a_node_that_later_fails_still_logged_its_declared_params(
        self, tmp_path, registry
    ):
        # Round-4 ruling (findings 1+2+3 and 5): the payload goes out at
        # run start and follows the DECLARED document, so what a node's
        # materialization later does cannot take its params back — a
        # crashed run is exactly the one you want to find in a sink by its
        # config. The reference that fails to resolve logs as written.
        self.register_memory()
        pipeline = banking_pipeline()
        pipeline["clip"] = NodeSpec(
            uses="synth-clip",
            inputs=dict(pipeline["clip"].inputs),
            params={**pipeline["clip"].params, "bad": "$splits.no_such_key"},
        )
        doc = bdoc(
            tmp_path,
            pipeline=pipeline,
            tracking=TrackingConfig(sinks=(SinkConfig(kind="memory"),)),
        )
        result = run_document(doc, asof=ASOF, registry=registry)
        logged = MemoryTracker.instances[-1].logged_params
        assert result.node_states["clip"] == "error"  # materialization failed…
        assert logged["clip.bad"] == "$splits.no_such_key"  # …the config landed
        assert logged["clip.lo"] == 0.02

    def test_a_node_the_run_never_reached_still_logged_its_declared_params(
        self, tmp_path, registry
    ):
        # Round-4 ruling (finding 5): 'once, at run start' means the
        # payload cannot depend on how far the run got — an aborted run
        # lands the same declared config a completed one does, which is
        # what lets a sink answer "which configs crash".
        self.register_memory()
        pipeline = banking_pipeline()
        pipeline["qhat"] = NodeSpec(
            uses="synth-train",
            mode="train",
            inputs={"events": "$clip.events"},
            params={"min_train": 10_000},
        )
        doc = bdoc(
            tmp_path,
            pipeline=pipeline,
            tracking=TrackingConfig(sinks=(SinkConfig(kind="memory"),)),
        )
        result = run_document(doc, asof=ASOF, registry=registry)
        logged = MemoryTracker.instances[-1].logged_params
        assert result.node_states["qhat"] == "error"
        assert logged["qhat.min_train"] == 10_000
        assert result.node_states["size"] == "not_run"
        assert logged["size.stake_frac"] == 0.1  # declared, so still logged

    def test_sink_closes_even_when_a_node_errors(self, tmp_path, registry):
        self.register_memory()
        pipeline = banking_pipeline()
        pipeline["qhat"] = NodeSpec(
            uses="synth-train",
            mode="train",
            inputs={"events": "$clip.events"},
            params={"min_train": 10_000},
        )
        doc = bdoc(
            tmp_path,
            pipeline=pipeline,
            tracking=TrackingConfig(sinks=(SinkConfig(kind="memory"),)),
        )
        result = run_document(doc, asof=ASOF, registry=registry)
        assert result.state == "error"
        assert MemoryTracker.instances[-1].closed

    def test_unknown_sink_kind_refused_before_any_write(self, tmp_path, registry):
        doc = bdoc(
            tmp_path,
            tracking=TrackingConfig(sinks=(SinkConfig(kind="wandb"),)),
        )
        with pytest.raises(ConfigError, match="not registered"):
            run_document(doc, asof=ASOF, registry=registry)
        assert not any(
            entry.startswith("synth-banking-") for entry in os.listdir(tmp_path)
        )

    def test_class_ref_sink_constructs_and_receives(self, tmp_path, registry):
        doc = bdoc(
            tmp_path,
            tracking=TrackingConfig(
                sinks=(SinkConfig(kind="dskit.pipeline.testing:MemoryTracker"),)
            ),
        )
        result = run_document(doc, asof=ASOF, registry=registry)
        sink = MemoryTracker.instances[-1]
        assert sink.logged_params["run_hash"] == result.run_hash and sink.closed

    def test_class_ref_sink_missing_the_seam_refused(self, tmp_path, registry):
        doc = bdoc(
            tmp_path,
            tracking=TrackingConfig(
                sinks=(SinkConfig(kind="tests.pipeline.refhelpers:NotASink"),)
            ),
        )
        with pytest.raises(ConfigError, match="Tracker seam"):
            run_document(doc, asof=ASOF, registry=registry)


class TestSplitsRefs:
    def base_pipeline(self):
        return {
            "events": NodeSpec(uses="synth-events", params={"n_events": 8}),
            "rep": NodeSpec(
                uses="synth-report",
                inputs={"n": "$events.newest_ms"},
                params={"cut": "$splits.train_end_ms"},
            ),
        }

    def test_splits_fields_materialize_into_params(self, tmp_path, registry):
        doc = bdoc(tmp_path, pipeline=self.base_pipeline())
        result = run_document(doc, asof=ASOF, registry=registry)
        assert result.state == "ran"
        report = read_json(
            result.run_dir, os.path.join("artifacts", "rep", "report.json")
        )
        assert report["n"] > 0

    def test_unknown_splits_field_fails_the_node_loudly(self, tmp_path, registry):
        pipeline = self.base_pipeline()
        pipeline["rep"] = NodeSpec(
            uses="synth-report",
            inputs={"n": "$events.newest_ms"},
            params={"cut": "$splits.t9"},
        )
        result = run_document(
            bdoc(tmp_path, pipeline=pipeline), asof=ASOF, registry=registry
        )
        assert result.state == "error"
        assert "no 't9'" in result.error and "train_end_ms" in result.error


class InfMetricsNode(Node):
    """A score-shaped node whose loss diverges (routine for logloss)."""

    role = "transform"

    def run(self, ctx, inputs):
        return {"metrics": {"loss": float("inf")}, "n": 3}


class EchoParamsNode(Node):
    """Returns the params it was constructed with — proves materialization."""

    role = "transform"

    def run(self, ctx, inputs):
        return {"params_seen": self.params}


class FlakySink:
    """A sink whose logging fails mid-run — telemetry must not kill runs."""

    def __init__(self, params):
        self.closed = False

    def log_params(self, mapping):
        raise ConnectionError("mlflow is down")

    def log_metrics(self, node, mapping):
        raise ConnectionError("mlflow is down")

    def close(self):
        self.closed = True


class CountingSink:
    """Tracks open/close pairing across driver refusals."""

    instances = []

    def __init__(self, params):
        self.closed = False
        CountingSink.instances.append(self)

    def log_params(self, mapping):
        pass

    def log_metrics(self, node, mapping):
        pass

    def close(self):
        self.closed = True


class TestReviewRegressions:
    """Each test pins one finding of the 2026-08-14 skeptic review."""

    def test_data_node_params_must_be_fully_literal(self, tmp_path, registry):
        # $splits/$prev in a data node's params used to ride through as
        # literals (the resolve-time instance was built from raw params).
        pipeline = {
            "events": NodeSpec(
                uses="synth-events", params={"start_ms": "$splits.train_end_ms"}
            )
        }
        with pytest.raises(ConfigError, match="fully literal"):
            run_document(
                bdoc(tmp_path, pipeline=pipeline), asof=ASOF, registry=registry
            )
        pipeline = {
            "events": NodeSpec(
                uses="synth-events",
                params={"seed": {"$prev": "events.newest_ms", "default": 7}},
            )
        }
        with pytest.raises(ConfigError, match="fully literal"):
            run_document(
                bdoc(tmp_path, pipeline=pipeline), asof=ASOF, registry=registry
            )

    def test_infinite_metrics_still_record(self, tmp_path, registry):
        # inf/NaN in outputs used to crash step 6 RECORD and strand the dir.
        pipeline = {
            "events": NodeSpec(uses="synth-events", params={"n_events": 8}),
            "diverged": NodeSpec(
                uses="tests.pipeline.test_driver:InfMetricsNode",
                inputs={"events": "$events.events"},
            ),
        }
        result = run_document(
            bdoc(tmp_path, pipeline=pipeline), asof=ASOF, registry=registry
        )
        assert result.state == "ran"
        record = read_json(result.run_dir, os.path.join("nodes", "02-diverged.json"))
        assert record["status"] == "ok"
        assert read_json(result.run_dir, "result.json")["state"] == "ran"

    def test_flaky_sink_cannot_kill_the_run(self, tmp_path, registry):
        doc = bdoc(
            tmp_path,
            tracking=TrackingConfig(
                sinks=(SinkConfig(kind="tests.pipeline.test_driver:FlakySink"),)
            ),
        )
        result = run_document(doc, asof=ASOF, registry=registry)
        assert result.state == "ran"

    def test_the_run_hash_ignores_the_tracking_section(self, tmp_path, registry):
        """The driver's own copy of the exclusion list, pinned (Ruling 1).

        ``run_hash`` is computed from the document minus
        ``DOC_NON_IDENTITY_SECTIONS``, in the driver rather than through
        ``PipelineDocument.hash`` — a second copy of the recipe, so a
        second place ``tracking`` could stay graded. Two documents
        differing ONLY in whether they declare a sink therefore have to
        name the same run directory, which the occupied-dir refusal
        reports for us.
        """
        run_document(bdoc(tmp_path), asof=ASOF, registry=registry)
        with pytest.raises(ValueError, match="already exists"):
            run_document(
                bdoc(
                    tmp_path,
                    tracking=TrackingConfig(sinks=(SinkConfig(kind="memory"),)),
                ),
                asof=ASOF,
                registry=registry,
            )

    def test_sinks_close_on_resolve_time_refusal(self, tmp_path, registry):
        # Identical document twice: the second run hits the occupied run
        # dir, which is what makes it a resolve-time refusal.
        doc = bdoc(
            tmp_path,
            tracking=TrackingConfig(
                sinks=(SinkConfig(kind="tests.pipeline.test_driver:CountingSink"),)
            ),
        )
        run_document(doc, asof=ASOF, registry=registry)
        with pytest.raises(ValueError, match="already exists"):
            run_document(doc, asof=ASOF, registry=registry)
        assert len(CountingSink.instances) >= 2
        assert all(s.closed for s in CountingSink.instances[-2:])

    def test_same_asof_prev_run_picked_by_mtime_not_hash(self, tmp_path, registry):
        # Two same-day prior runs: the newer by mtime must win, whatever
        # the hash suffix's hex ordering says.
        older = tmp_path / "synth-banking-2026-01-01-ffffffff"
        newer = tmp_path / "synth-banking-2026-01-01-00000000"
        for i, d in enumerate((older, newer)):
            d.mkdir()
            (d / "carry.json").write_text(
                json.dumps({"size": {"final_bankroll": 111.0 * (i + 1)}})
            )
            os.utime(d, (1000 + i, 1000 + i))
        result = run_document(bdoc(tmp_path), asof="2026-01-08", registry=registry)
        assert result.prev_run == str(newer)
        # bankroll 222 carried: final = 222 * 1.02
        assert result.outputs["size"]["final_bankroll"] == pytest.approx(226.44)

    def test_tuple_params_materialize(self, tmp_path, registry):
        pipeline = {
            "events": NodeSpec(uses="synth-events", params={"n_events": 8}),
            "echo": NodeSpec(
                uses="tests.pipeline.test_driver:EchoParamsNode",
                inputs={"events": "$events.events"},
                params={"srcs": ("$events.newest_ms",)},
            ),
        }
        result = run_document(
            bdoc(tmp_path, pipeline=pipeline), asof=ASOF, registry=registry
        )
        assert result.state == "ran"
        # The ref inside the tuple resolved — no literal ride-through.
        (value,) = result.outputs["echo"]["params_seen"]["srcs"]
        assert value == result.outputs["events"]["newest_ms"]

    def test_prev_default_must_be_literal(self):
        with pytest.raises(ConfigError, match="default must be a literal"):
            NodeSpec(
                uses="k",
                params={"w": {"$prev": "e.seen", "default": "$events.newest_ms"}},
            )

    def test_params_only_stat_test_reference_does_not_gate_capital(self, registry):
        from dskit.pipeline.planner import plan as plan_document

        pipeline = banking_pipeline()
        pipeline["size"] = NodeSpec(
            uses="synth-capital",
            inputs={"signal": "$qhat.signal", "survivors": "$family.instruments"},
            params={"bankroll": 100.0, "note": "$edge_test.pvalues"},
        )
        with pytest.raises(ConfigError, match="un-gated capital"):
            plan_document(banking_document(pipeline=pipeline), registry)

    def test_trailing_train_days_bound_refuses_to_materialize(self):
        bounded = TrailingSplitSpec(test_days=14, val_days=28, train_days=30)
        with pytest.raises(ValueError, match="I-223"):
            bounded.materialize(100 * 24 * 60 * 60 * 1000)


def _live_streams(logger):
    """The driver's live-stderr kind: StreamHandlers that are not files."""
    return sum(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in logger.handlers
    )


class LoggerProbeNode(Node):
    """Logs one INFO line and reports the live handlers it saw mid-run."""

    role = "transform"
    outputs = ("probe",)

    def run(self, ctx, inputs):
        self.log.info("probe-sentinel-line")
        return {
            "probe": {
                "pipeline_live": _live_streams(logging.getLogger("dskit.pipeline")),
                "root_live": _live_streams(logging.getLogger()),
            }
        }


class RaisingProbeNode(Node):
    """Raises, carrying the mid-run live-handler count in the message."""

    role = "transform"

    def run(self, ctx, inputs):
        n = _live_streams(logging.getLogger("dskit.pipeline"))
        raise RuntimeError(f"boom live-streams-mid-error={n}")


def probe_doc(tmp_path, node="LoggerProbeNode"):
    pipeline = {
        "events": NodeSpec(uses="synth-events", params={"n_events": 8}),
        "probe": NodeSpec(
            uses=f"tests.pipeline.test_driver:{node}",
            inputs={"events": "$events.events"},
        ),
    }
    return PipelineDocument(
        name="stream-probe",
        pipeline=pipeline,
        outputs=OutputsConfig(run_root=str(tmp_path)),
    )


def operator_terminal():
    """Strip pytest's own live StreamHandlers from the root logger.

    The driver streams only when the caller has no live (non-file)
    StreamHandler anywhere — and pytest's log-capture handlers are
    exactly that, so under test the guard sees an embedding application
    and rightly declines. Stripping them simulates the bare operator
    terminal the feature exists for. Called from the test BODY, not a
    fixture: fixtures run in the setup phase and pytest re-attaches its
    capture handlers when the call phase opens. No restore: pytest's own
    end-of-phase removal is membership-checked (a no-op here) and it
    re-attaches the same reused handlers at the next phase boundary.
    The pipeline logger is swept too, so a leaked handler (the exact
    defect the teardown tests exist to catch) cannot ride into the next
    test's ``before`` snapshot and mask its assertions.
    """
    for logger in (logging.getLogger(), logging.getLogger("dskit.pipeline")):
        for handler in list(logger.handlers):
            if isinstance(handler, logging.StreamHandler) and not isinstance(
                handler, logging.FileHandler
            ):
                logger.removeHandler(handler)


class TestLiveStderrStreaming:
    """ADR-0025 residual: INFO lines stream live to stderr during a run."""

    def test_node_info_lines_stream_bare_to_stderr(self, tmp_path, registry, capsys):
        operator_terminal()
        result = run_document(probe_doc(tmp_path), asof=ASOF, registry=registry)
        assert result.state == "ran"
        err = capsys.readouterr().err
        # Bare %(message)s lines — no asctime/name/level prefix — and the
        # driver's own narration streams alongside the node's records.
        assert "probe-sentinel-line" in err.splitlines()
        assert "node probe: start" in err
        # Streamed, not doubled: run.log still carries each line once.
        with open(os.path.join(result.run_dir, "run.log"), encoding="utf-8") as fh:
            assert fh.read().count("probe-sentinel-line") == 1

    def test_handler_on_pipeline_logger_only_during_the_run(self, tmp_path, registry):
        operator_terminal()
        pipeline_logger = logging.getLogger("dskit.pipeline")
        before = list(pipeline_logger.handlers)
        result = run_document(probe_doc(tmp_path), asof=ASOF, registry=registry)
        probe = result.outputs["probe"]["probe"]
        assert probe["pipeline_live"] == 1  # installed on dskit.pipeline…
        assert probe["root_live"] == 0  # …never on the root logger
        assert pipeline_logger.handlers == before  # and removed at the end

    def test_handler_removed_when_a_node_raises(self, tmp_path, registry):
        operator_terminal()
        pipeline_logger = logging.getLogger("dskit.pipeline")
        before = list(pipeline_logger.handlers)
        result = run_document(
            probe_doc(tmp_path, node="RaisingProbeNode"), asof=ASOF, registry=registry
        )
        assert result.state == "error"
        # Streaming was live when the node blew up…
        assert "live-streams-mid-error=1" in result.error
        # …and the failure path still tears it down: nothing leaks.
        assert pipeline_logger.handlers == before

    def test_a_callers_own_stream_handler_is_never_doubled(self, tmp_path, registry):
        operator_terminal()
        own = logging.StreamHandler(io.StringIO())
        root = logging.getLogger()
        root.addHandler(own)
        try:
            result = run_document(probe_doc(tmp_path), asof=ASOF, registry=registry)
        finally:
            root.removeHandler(own)
        probe = result.outputs["probe"]["probe"]
        assert probe["pipeline_live"] == 0  # driver declined — caller streams
        # The caller's handler still gets the lines, via propagation.
        assert "probe-sentinel-line" in own.stream.getvalue()


class TestHelpers:
    def test_summarize_shapes(self):
        assert _summarize(3.5) == 3.5 and _summarize(True) is True
        assert _summarize(None) is None
        truncated = _summarize("x" * 300)
        assert truncated.endswith("…") and len(truncated) == 201
        assert _summarize("short") == "short"
        assert _summarize(list(range(5))) == {"type": "list", "len": 5}
        assert _summarize({"a": 1}) == {"type": "dict", "len": 1}
        assert _summarize(object())["type"] == "object"

    def test_carryable_rules(self):
        assert _carryable(1000.0) == (1000.0, True)
        assert _carryable(object()) == (None, False)
        assert _carryable("x" * 30_000) == (None, False)
        stream = [{"i": 0}] * 10_001
        assert _too_big_to_carry(stream) is True
        assert _carryable(stream) == (None, False)
        summary = _summarize(stream)
        assert _is_summary(summary)
        assert _summarize(summary) == summary
        assert _carryable(summary) == (None, False)


class FatSource(Node):
    """Emit ``n`` tiny records so release can fire without a real tape."""

    role = "data"
    outputs = ("records", "n")

    def fingerprint(self):
        return {"kind": "fat", "n": int(self.params["n"])}

    def run(self, ctx, inputs):
        n = int(self.params["n"])
        return {"records": [{"i": i} for i in range(n)], "n": n}


class HeadRows(Node):
    """Keep the first upstream row so the source is spent."""

    role = "transform"
    outputs = ("records",)

    def run(self, ctx, inputs):
        rows = inputs["records"]
        return {"records": list(rows[:1])}


def _fat_registry():
    registry = NodeKindRegistry()
    registry.register("fat-src", FatSource)
    registry.register("head-rows", HeadRows)
    return registry


def _fat_doc(tmp_path, n, consumers=("kept",)):
    pipeline = {
        "src": NodeSpec(uses="fat-src", params={"n": n}),
    }
    for key in consumers:
        pipeline[key] = NodeSpec(
            uses="head-rows",
            inputs={"records": "$src.records"},
        )
    return PipelineDocument(
        name="release-spent",
        pipeline=pipeline,
        outputs=OutputsConfig(run_root=str(tmp_path)),
    )


class TestSpentRelease:
    def test_release_min_len_is_the_one_name(self):
        assert _RELEASE_MIN_LEN == 256

    def test_spent_stream_is_summarized_after_its_last_reader(self, tmp_path):
        n = _RELEASE_MIN_LEN
        result = run_document(
            _fat_doc(tmp_path, n, consumers=("kept",)),
            asof=ASOF,
            registry=_fat_registry(),
        )
        assert result.state == "ran"
        assert result.outputs["src"]["records"] == {"type": "list", "len": n}
        assert result.outputs["src"]["n"] == n
        assert result.outputs["kept"]["records"] == [{"i": 0}]
        record = read_json(os.path.join(result.run_dir, "nodes"), "01-src.json")
        assert record["outputs"]["records"] == {"type": "list", "len": n}
        carry = read_json(result.run_dir, "carry.json")
        assert "records" not in carry.get("src", {})
        assert carry["src"]["n"] == n

    def test_two_readers_keep_the_stream_until_both_finish(self, tmp_path):
        n = _RELEASE_MIN_LEN
        result = run_document(
            _fat_doc(tmp_path, n, consumers=("left", "right")),
            asof=ASOF,
            registry=_fat_registry(),
        )
        assert result.outputs["src"]["records"] == {"type": "list", "len": n}
        assert result.outputs["left"]["records"] == [{"i": 0}]
        assert result.outputs["right"]["records"] == [{"i": 0}]

    def test_a_short_stream_stays_for_the_caller(self, tmp_path):
        result = run_document(
            _fat_doc(tmp_path, 12, consumers=("kept",)),
            asof=ASOF,
            registry=_fat_registry(),
        )
        assert result.outputs["src"]["records"] == [{"i": i} for i in range(12)]

    def test_node_metrics_extraction(self):
        out = _node_metrics(
            {
                "final_bankroll": 1020.0,
                "ok": True,
                "metrics": {"loss": 0.2, "n": 96, "tag": "val"},
                "positions": {"A": 1.0},
            }
        )
        assert out == {
            "final_bankroll": 1020.0,
            "metrics.loss": 0.2,
            "metrics.n": 96,
        }
