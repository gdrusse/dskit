"""The run evaluator: ``run-report`` (I-232).

The failure this kind exists to prevent: a run exits 0, every node is
green, the model beats its baseline, every instrument survives the edge
test — and ZERO lots are deployed, with no artifact saying so. These
tests pin the condition itself, not just the plumbing: the LOUD flag
must fire, it must reach ``report.md``, and it must NOT change the
driver's exit code (owner ruling, 2026-08-15 — report only).
"""

import json
import os

import pytest

from dskit.pipeline.base import ConfigError
from dskit.pipeline.document import NodeSpec
from dskit.pipeline.driver import run_document
from dskit.pipeline.kinds_report import RunReport, register
from dskit.pipeline.node import Node, NodeContext, NodeKindRegistry
from tests.pipeline.test_kinds_stats import (
    edge_document,
    edge_pipeline,
    private_registry,
)

ASOF = "2026-01-01"


class StubSizer(Node):
    """A ``capital`` node that deploys exactly the lots its params name.

    Local to this file on purpose: the reproduction needs a sizer whose
    lot count is STATED, and the document grammar rightly refuses a
    literal on an input port — every input must wire a real node output.
    So the zero comes from a node, through the same reference machinery a
    real sizer would use.
    """

    role = "capital"
    outputs = ("positions", "metrics")

    def run(self, ctx, inputs):
        lots = int(self.params.get("lots", 0))
        survivors = sorted(inputs["survivors"])
        positions = {survivors[0]: {"c|yes": lots}} if (lots and survivors) else {}
        return {"positions": positions, "metrics": {"n_lots": lots}}


@pytest.fixture
def ctx(tmp_path):
    return NodeContext(name="report", asof=ASOF, run_dir=str(tmp_path))


def run(ctx, **inputs):
    node = RunReport("report", inputs.pop("_params", {}))
    return node, node.run(ctx, inputs)


def artifact(node, ctx, name="evidence.json"):
    path = os.path.join(node.artifact_dir(ctx), name)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh) if name.endswith(".json") else fh.read()


# ---------------------------------------------------------------------------
# Params / inputs
# ---------------------------------------------------------------------------


class TestParams:
    def test_defaults_are_valid(self):
        assert RunReport.validate_params({}) == []

    def test_title_accepted(self):
        assert RunReport.validate_params({"title": "Q3 edge run"}) == []

    def test_unknown_knob_refused_by_name(self):
        (problem,) = RunReport.validate_params({"ttile": "typo"})
        assert "ttile" in problem and "unknown param" in problem

    @pytest.mark.parametrize("title", [3, None, ["a"]])
    def test_bad_title(self, title):
        (problem,) = RunReport.validate_params({"title": title})
        assert "title" in problem

    def test_construction_refuses_bad_params(self):
        with pytest.raises(ConfigError, match="unknown param"):
            RunReport("report", {"nope": 1})


class TestInputs:
    def test_everything_optional(self):
        assert RunReport("r").validate_inputs({}) == []

    @pytest.mark.parametrize(
        "port", ["training", "validation", "edge", "sizing", "replay"]
    )
    def test_stage_port_must_be_a_dict(self, port):
        (problem,) = RunReport("r").validate_inputs({port: [1, 2]})
        assert port in problem

    def test_survivors_must_be_a_list(self):
        (problem,) = RunReport("r").validate_inputs({"survivors": "AAA"})
        assert "survivors" in problem

    @pytest.mark.parametrize("lots", [1.5, True, "0"])
    def test_lots_must_be_an_int(self, lots):
        (problem,) = RunReport("r").validate_inputs({"lots": lots})
        assert "lots" in problem

    def test_zero_lots_is_a_legal_input_not_a_missing_one(self):
        # The whole point: lots == 0 must be WIRED and reported, never
        # confused with "not wired".
        assert RunReport("r").validate_inputs({"lots": 0}) == []


# ---------------------------------------------------------------------------
# The LOUD flag
# ---------------------------------------------------------------------------


class TestTheLoudFlag:
    def test_survivors_with_zero_lots_is_loud(self, ctx):
        _node, out = run(ctx, survivors=["AAA", "BBB"], lots=0)
        (flag,) = out["flags"]
        assert flag["level"] == "LOUD"
        assert flag["code"] == "survivors-but-zero-lots"
        assert "AAA" in flag["message"] and "BBB" in flag["message"]
        assert out["summary"]["loud"] == 1

    def test_survivors_with_lots_raises_nothing(self, ctx):
        _node, out = run(ctx, survivors=["AAA"], lots=12)
        assert out["flags"] == [] and out["summary"]["loud"] == 0

    def test_no_survivors_and_no_lots_is_a_clean_nogo(self, ctx):
        # A NO-GO that deploys nothing is the system working, not a defect.
        _node, out = run(ctx, survivors=[], lots=0)
        assert out["flags"] == []

    def test_lots_without_survivors_is_also_loud(self, ctx):
        # The mirror image: capital moved on an edge nobody declared.
        _node, out = run(ctx, survivors=[], lots=5)
        (flag,) = out["flags"]
        assert flag["level"] == "LOUD" and flag["code"] == "zero-survivors-but-lots"

    @pytest.mark.parametrize(
        "inputs",
        [
            {"survivors": ["AAA"]},
            {"lots": 0},
            {},
        ],
    )
    def test_an_unevaluable_check_says_so_rather_than_passing_quietly(
        self, ctx, inputs
    ):
        # Silence is the bug. A half-wired report must announce that the
        # condition could not be evaluated, not render a clean page.
        _node, out = run(ctx, **inputs)
        (flag,) = out["flags"]
        assert flag["code"] == "deploy-check-not-evaluable"
        assert out["summary"]["loud"] == 0  # a gap is a note, never LOUD

    def test_the_flag_does_not_depend_on_stage_evidence(self, ctx):
        # Wired operands, no evidence dicts at all: the flag still fires.
        _node, out = run(ctx, survivors=["AAA"], lots=0)
        assert out["summary"]["stages"] == 0
        assert out["flags"][0]["level"] == "LOUD"


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


class TestArtifacts:
    def test_json_carries_the_full_record(self, ctx):
        node, out = run(
            ctx,
            survivors=["AAA"],
            lots=0,
            sizing={
                "stage": "sizing",
                "totals": {"lots": 0, "n_entered": 4},
                "instruments": {"AAA": {"lots": 0, "n_entered_zero_lots": 4}},
            },
        )
        payload = artifact(node, ctx)
        assert out["path"].endswith("evidence.json")
        assert payload["deployment"] == {
            "survivors": ["AAA"],
            "n_survivors": 1,
            "lots": 0,
        }
        assert payload["stages"]["sizing"]["totals"]["n_entered"] == 4
        assert payload["flags"][0]["code"] == "survivors-but-zero-lots"

    def test_markdown_leads_with_the_loud_section(self, ctx):
        node, _out = run(ctx, survivors=["AAA"], lots=0)
        text = artifact(node, ctx, "evidence.md")
        assert text.index("## LOUD") < text.index("## Deployment")
        assert "survivors-but-zero-lots" in text

    def test_per_instrument_rows_render_as_a_table(self, ctx):
        node, _out = run(
            ctx,
            survivors=["AAA"],
            lots=3,
            validation={
                "stage": "validation",
                "split": "test",
                "instruments": {
                    "AAA": {"loss": 0.11, "n_scored": 40},
                    "BBB": {"loss": 0.22, "n_scored": 10},
                },
            },
        )
        text = artifact(node, ctx, "evidence.md")
        assert "split `test`" in text
        assert "| instrument | loss | n_scored |" in text
        assert "| AAA |" in text and "| BBB |" in text

    def test_a_field_only_one_row_carries_still_appears(self, ctx):
        node, _out = run(
            ctx,
            survivors=["AAA"],
            lots=1,
            sizing={
                "instruments": {"AAA": {"lots": 1, "note": "x"}, "BBB": {"lots": 0}}
            },
        )
        text = artifact(node, ctx, "evidence.md")
        # BBB has no "note" — the column must still exist, filled with a dash,
        # rather than the column vanishing because one row lacked it.
        assert "note" in text and "| BBB | 0 | — |" in text

    def test_unknown_evidence_keys_are_rendered_not_dropped(self, ctx):
        node, _out = run(
            ctx,
            survivors=["AAA"],
            lots=1,
            replay={"stage": "replay", "surprise_field": 7},
        )
        assert "surprise_field" in artifact(node, ctx, "evidence.md")

    def test_a_nested_row_table_renders_as_a_table_not_a_key_count(self, ctx):
        # Reliability by price bucket arrives as a key the convention does
        # not name. Collapsing it to "(3 key(s))" would hide the whole
        # calibration breakdown — the exact class of loss being fixed.
        node, _out = run(
            ctx,
            survivors=["AAA"],
            lots=1,
            validation={
                "stage": "validation",
                "reliability": {
                    "0.5-0.6": {"n": 10, "observed_rate": 0.5},
                    "0.7-0.8": {"n": 20, "observed_rate": 0.8},
                },
            },
        )
        text = artifact(node, ctx, "evidence.md")
        assert "key(s)" not in text
        assert "| reliability | n | observed_rate |" in text
        assert "| 0.7-0.8 | 20 | 0.8 |" in text

    def test_a_long_table_truncates_the_render_never_the_record(self, ctx):
        rows = {f"e{i:03d}": {"lots": i} for i in range(40)}
        node, _out = run(
            ctx, survivors=["AAA"], lots=1, sizing={"stage": "sizing", "events": rows}
        )
        text = artifact(node, ctx, "evidence.md")
        assert "15 more row(s)" in text and "stages.sizing.events" in text
        # The RECORD keeps all 40.
        payload = artifact(node, ctx)
        assert len(payload["stages"]["sizing"]["events"]) == 40

    def test_stages_render_in_pipeline_order(self, ctx):
        node, _out = run(
            ctx,
            survivors=["AAA"],
            lots=1,
            replay={"stage": "replay"},
            training={"stage": "training"},
            edge={"stage": "edge test"},
        )
        text = artifact(node, ctx, "evidence.md")
        assert text.index("training") < text.index("edge test") < text.index("replay")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_register_claims_the_owned_name(self):
        registry = NodeKindRegistry()
        register(registry)
        assert registry.get("run-report") == (RunReport, True)

    def test_register_is_idempotent(self):
        registry = NodeKindRegistry()
        register(registry)
        register(registry)  # must not raise on the duplicate
        assert "run-report" in registry


# ---------------------------------------------------------------------------
# The I-232 reproduction, through the real driver
# ---------------------------------------------------------------------------


class TestTheReproduction:
    """The planted-edge document — a run that exits 0 with both
    instruments surviving at p = 1/2001 — with the deployment wired to
    ZERO lots.

    This is a FIXTURE, not a backtest: the zero is stated by the document
    rather than produced by a solver, because the point under test is the
    REPORTING of that condition. What it proves is that the condition is
    no longer silent, and that saying it out loud does not change the
    run's verdict.
    """

    def _document(self, tmp_path, lots):
        pipeline = dict(edge_pipeline())
        pipeline["size"] = NodeSpec(
            uses="stub-sizer",
            inputs={"survivors": "$edge.survivors"},
            params={"lots": lots},
        )
        pipeline["report"] = NodeSpec(
            uses="run-report",
            inputs={
                "validation": "$val.evidence",
                "edge": "$edge.evidence",
                "survivors": "$edge.survivors",
                "lots": "$size.metrics.n_lots",
            },
            params={"title": "planted edge"},
        )
        return edge_document(tmp_path, pipeline, "kinds-report")

    def _registry(self):
        registry = private_registry()
        registry.register("stub-sizer", StubSizer)
        register(registry)
        return registry

    def test_zero_deployment_is_loud_in_report_md(self, tmp_path):
        result = run_document(
            self._document(tmp_path, 0), asof=ASOF, registry=self._registry()
        )
        # Exactly the I-232 run: green, GO, and nothing deployed.
        assert result.outputs["edge"]["survivors"] == ["SYNA", "SYNB"]
        assert result.outputs["val"]["metrics"]["beats_baseline"] is True

        with open(os.path.join(result.run_dir, "report.md"), encoding="utf-8") as fh:
            report = fh.read()
        assert "## ⚠ LOUD" in report
        assert "survivors-but-zero-lots" in report
        # Above the node table, which is the line that used to be the
        # entire report and is true of the broken run and the healthy one.
        assert report.index("LOUD") < report.index("| node | role |")

    def test_the_flag_does_not_change_the_exit_code(self, tmp_path):
        # Owner ruling 2026-08-15: REPORT ONLY. A finding a human reads,
        # never a machine verdict — it may be a real economic result.
        result = run_document(
            self._document(tmp_path, 0), asof=ASOF, registry=self._registry()
        )
        assert result.state == "ran" and result.exit_code == 0
        assert result.halted_at == "" and result.node_states["report"] == "ok"

    def test_a_deploying_run_reports_no_loud_section(self, tmp_path):
        result = run_document(
            self._document(tmp_path, 7), asof=ASOF, registry=self._registry()
        )
        with open(os.path.join(result.run_dir, "report.md"), encoding="utf-8") as fh:
            report = fh.read()
        assert "LOUD" not in report
        assert result.exit_code == 0

    def test_the_evidence_artifact_carries_the_per_instrument_rows(self, tmp_path):
        result = run_document(
            self._document(tmp_path, 0), asof=ASOF, registry=self._registry()
        )
        path = os.path.join(result.run_dir, "artifacts", "report", "evidence.json")
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        edge = payload["stages"]["edge"]["instruments"]
        assert edge["SYNA"]["survived"] is True
        assert edge["SYNA"]["p_value"] == 1 / 2001
        validation = payload["stages"]["validation"]
        assert validation["split"] == "val"
        # Coverage: the denominator the old report never carried.
        assert validation["totals"]["rows_scored"] == 384
        assert validation["totals"]["rows_in_split"] == 384
        assert set(validation["instruments"]) == {"SYNA", "SYNB"}
        assert validation["reliability"]  # calibration by price bucket
