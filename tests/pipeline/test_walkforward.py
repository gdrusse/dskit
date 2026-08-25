"""Walk-forward evaluation (ADR-0027): the spec grammar, the embargoed
splits, and the driver's per-fold execution + summary."""

from __future__ import annotations

import json
import os

import pytest

from dskit.pipeline.base import ConfigError, OutputsConfig, TimeSplitConfig
from dskit.pipeline.document import (
    NodeSpec,
    PipelineDocument,
    TrailingSplitSpec,
    WalkForwardSpec,
)
from dskit.pipeline.driver import run_walk_forward
from dskit.pipeline.node import Node

DAY = 24 * 60 * 60 * 1000
ASOF = "2026-01-01"


class SplitProbe(Node):
    """Echoes the fold's materialized cuts: the score IS train_end_ms, so
    every fold's objective is distinct and exactly predictable."""

    role = "transform"
    outputs = ("score", "cuts")

    def run(self, ctx, inputs):
        return {"score": float(ctx.splits_info["train_end_ms"]), "cuts": dict(ctx.splits_info)}


class LateFoldGate(Node):
    """NO-GO once the fold's train cut passes ``halt_after_ms`` — how the
    halted-fold path is exercised."""

    role = "gate"
    outputs = ("verdict",)

    @classmethod
    def validate_params(cls, params):
        return [] if set(params) <= {"halt_after_ms"} else ["unknown params"]

    def run(self, ctx, inputs):
        late = ctx.splits_info["train_end_ms"] > self.params["halt_after_ms"]
        return {"verdict": "NO-GO" if late else "GO"}


def probe_doc(tmp_path, wf, *, gate_after=None):
    pipeline = {
        "events": NodeSpec(
            uses="dskit.pipeline.synthetic_nodes:SynthEvents",
            params={"n_events": 4},
        ),
    }
    probe_inputs = {"events": "$events.events"}
    if gate_after is not None:
        pipeline["gate"] = NodeSpec(
            uses="tests.pipeline.test_walkforward:LateFoldGate",
            inputs={"events": "$events.events"},
            params={"halt_after_ms": gate_after},
        )
        probe_inputs["verdict"] = "$gate.verdict"
    pipeline["probe"] = NodeSpec(
        uses="tests.pipeline.test_walkforward:SplitProbe", inputs=probe_inputs
    )
    return PipelineDocument(
        name="wfdemo",
        pipeline=pipeline,
        outputs=OutputsConfig(run_root=str(tmp_path)),
        walkforward=wf,
    )


def wf_spec(**overrides):
    base = {
        "objective": "$probe.score",
        "val_days": 7,
        "folds": ["2025-01-01", "2025-02-01"],
    }
    base.update(overrides)
    return WalkForwardSpec(**base)


# -- the spec grammar ----------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "needle"),
    [
        ({"objective": ""}, "objective"),
        ({"objective": "no-dollar"}, "objective"),
        ({"val_days": 0}, "val_days"),
        ({"embargo_days": -1}, "embargo_days"),
        ({"select": "best"}, "select"),
        ({"folds": []}, "folds"),
        ({"folds": ["01-01-2025"]}, "folds"),
        ({"folds": ["2025-02-01", "2025-01-01"]}, "ascending"),
        ({"folds": ["2025-01-01", "2025-01-01"]}, "ascending"),
        ({"folds": None}, "no folds declared"),
        ({"first": "2025-01-01", "step_days": 7, "count": 2}, "both"),
        ({"folds": None, "first": "bad", "step_days": 7, "count": 2}, "first"),
        ({"folds": None, "first": "2025-01-01", "count": 2}, "step_days"),
        ({"folds": None, "first": "2025-01-01", "step_days": 7}, "count"),
    ],
)
def test_spec_validation_refuses_by_name(overrides, needle):
    with pytest.raises(ConfigError, match=needle):
        wf_spec(**overrides)


def test_fold_cutoffs_explicit_and_generated():
    assert wf_spec().fold_cutoffs() == ("2025-01-01", "2025-02-01")
    generated = wf_spec(folds=None, first="2025-01-01", step_days=7, count=3)
    assert generated.fold_cutoffs() == ("2025-01-01", "2025-01-08", "2025-01-15")


def test_spec_round_trip_emits_only_the_active_declaration():
    explicit = wf_spec().to_obj()
    assert "folds" in explicit and "first" not in explicit
    assert WalkForwardSpec.from_obj(explicit).fold_cutoffs() == (
        "2025-01-01",
        "2025-02-01",
    )
    generated = wf_spec(folds=None, first="2025-01-01", step_days=7, count=2)
    obj = generated.to_obj()
    assert "folds" not in obj and obj["count"] == 2
    with pytest.raises(ConfigError, match="every_fold"):
        WalkForwardSpec.from_obj({**explicit, "every_fold": True})


# -- the document integration --------------------------------------------------


def test_document_carries_and_hashes_the_section(tmp_path):
    doc = probe_doc(tmp_path, wf_spec())
    obj = doc.to_obj()
    assert obj["walkforward"]["objective"] == "$probe.score"
    round_tripped = PipelineDocument.from_obj(obj)
    assert round_tripped.hash == doc.hash
    # The section is IDENTITY (unlike schedule): a different fold plan is
    # a different experiment.
    other = probe_doc(tmp_path, wf_spec(folds=["2025-01-01", "2025-03-01"]))
    assert other.hash != doc.hash


def test_documents_without_the_section_do_not_emit_the_key(tmp_path):
    doc = probe_doc(tmp_path, wf_spec())
    plain = PipelineDocument(
        name="plain", pipeline=dict(doc.pipeline), outputs=doc.outputs
    )
    assert "walkforward" not in plain.to_obj()  # pre-ADR-0027 hashes must not move


def test_objective_must_reference_a_declared_node(tmp_path):
    with pytest.raises(ConfigError, match="DECLARED node"):
        probe_doc(tmp_path, wf_spec(objective="$nope.score"))


# -- embargoed time splits (the split-level half of ADR-0027) ------------------


class _Rec:
    def __init__(self, t):
        self.asof_ms = t


def test_val_start_opens_an_embargo_band():
    cuts = TimeSplitConfig(
        train_end_ms=10 * DAY,
        val_start_ms=12 * DAY,
        val_end_ms=20 * DAY,
        test_end_ms=25 * DAY,
    )
    assert cuts.split_of(_Rec(10 * DAY)) == "train"
    assert cuts.split_of(_Rec(11 * DAY)) is None  # embargoed: NO split
    assert cuts.split_of(_Rec(12 * DAY)) == "val"
    assert cuts.split_of(_Rec(20 * DAY)) == "val"
    assert cuts.split_of(_Rec(21 * DAY)) == "test"
    assert cuts.split_of(_Rec(26 * DAY)) is None


def test_val_start_invariants_and_identity_omission():
    with pytest.raises(ConfigError, match="val_start_ms"):
        TimeSplitConfig(
            train_end_ms=10, val_start_ms=10, val_end_ms=20, test_end_ms=30
        )
    with pytest.raises(ConfigError, match="val_start_ms"):
        TimeSplitConfig(
            train_end_ms=10, val_start_ms=25, val_end_ms=20, test_end_ms=30
        )
    plain = TimeSplitConfig(train_end_ms=10, val_end_ms=20, test_end_ms=30)
    assert "val_start_ms" not in plain.to_obj()  # existing identities must not move
    banded = TimeSplitConfig(
        train_end_ms=10, val_start_ms=15, val_end_ms=20, test_end_ms=30
    )
    assert TimeSplitConfig.from_obj(banded.to_obj()) == banded


def test_trailing_embargo_materializes_the_band():
    spec = TrailingSplitSpec(test_days=5, val_days=10, embargo_days=3)
    cuts = spec.materialize(100 * DAY)
    assert cuts.test_end_ms == 100 * DAY
    assert cuts.val_end_ms == 95 * DAY
    assert cuts.val_start_ms == 85 * DAY
    assert cuts.train_end_ms == 82 * DAY  # the embargo came out of TRAIN's tail
    assert "embargo_days" not in TrailingSplitSpec(test_days=5, val_days=10).to_obj()
    assert TrailingSplitSpec.from_obj(spec.to_obj()) == spec
    no_embargo = TrailingSplitSpec(test_days=5, val_days=10).materialize(100 * DAY)
    assert no_embargo.val_start_ms is None


# -- the driver: one run per fold + a summary ----------------------------------


def read_json(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as fh:
        return json.load(fh)


def test_walk_forward_runs_each_fold_and_aggregates(tmp_path):
    result = run_walk_forward(probe_doc(tmp_path, wf_spec()), asof=ASOF)
    assert result.state == "ran"
    assert result.exit_code == 0
    assert [f["cutoff"] for f in result.folds] == ["2025-01-01", "2025-02-01"]
    # The probe's score IS each fold's train_end_ms — pinned arithmetic.
    from dskit.pipeline.driver import _cutoff_ms

    expected = [float(_cutoff_ms(c)) for c in ("2025-01-01", "2025-02-01")]
    assert [f["score"] for f in result.folds] == expected
    assert result.aggregate["n_scored"] == 2
    assert result.aggregate["best_cutoff"] == "2025-01-01"  # select=min
    # Every fold is an ordinary run dir of its own series.
    for fold in result.folds:
        assert os.path.basename(fold["run_dir"]).startswith(
            f"wfdemo-wf-{fold['cutoff']}-{ASOF}"
        )
        assert os.path.isfile(os.path.join(fold["run_dir"], "result.json"))
    summary = read_json(result.summary_dir, "walkforward.json")
    assert summary["state"] == "ran"
    assert summary["aggregate"]["mean"] == pytest.approx(sum(expected) / 2)
    assert os.path.isfile(os.path.join(result.summary_dir, "report.md"))


def test_walk_forward_generated_schedule_and_embargo_cuts(tmp_path):
    wf = wf_spec(
        folds=None, first="2025-01-01", step_days=7, count=2, embargo_days=3
    )
    result = run_walk_forward(probe_doc(tmp_path, wf), asof=ASOF)
    assert [f["cutoff"] for f in result.folds] == ["2025-01-01", "2025-01-08"]
    from dskit.pipeline.driver import _cutoff_ms

    cuts = read_json(result.folds[0]["run_dir"], "resolved.json")["splits"]
    c = _cutoff_ms("2025-01-01")
    assert cuts["val_start_ms"] == c
    assert cuts["train_end_ms"] == c - 3 * DAY
    assert cuts["val_end_ms"] == c + 7 * DAY


def test_a_halted_fold_is_a_result_and_later_folds_still_run(tmp_path):
    from dskit.pipeline.driver import _cutoff_ms

    gate_after = _cutoff_ms("2025-01-15")  # fold 1 passes, fold 2 halts
    doc = probe_doc(tmp_path, wf_spec(), gate_after=gate_after)
    result = run_walk_forward(doc, asof=ASOF)
    assert result.state == "halted"
    assert result.exit_code == 3
    assert result.folds[0]["state"] == "ran"
    assert result.folds[0]["score"] is not None
    assert result.folds[1]["state"] == "halted"
    assert result.folds[1]["score"] is None
    assert result.aggregate["n_scored"] == 1


def test_an_unreadable_objective_is_a_fold_error_that_stops_the_plan(tmp_path):
    doc = probe_doc(tmp_path, wf_spec(objective="$probe.no_such_output"))
    result = run_walk_forward(doc, asof=ASOF)
    assert result.state == "error"
    assert result.exit_code == 1
    assert result.folds[0]["state"] == "error"
    assert len(result.folds) == 1  # the plan stopped at the erroring fold


def test_missing_section_and_occupied_summary_refuse(tmp_path):
    plain = PipelineDocument(
        name="plain",
        pipeline={
            "events": NodeSpec(
                uses="dskit.pipeline.synthetic_nodes:SynthEvents",
                params={"n_events": 4},
            )
        },
        outputs=OutputsConfig(run_root=str(tmp_path)),
    )
    with pytest.raises(ConfigError, match="no walkforward section"):
        run_walk_forward(plain, asof=ASOF)
    doc = probe_doc(tmp_path, wf_spec())
    run_walk_forward(doc, asof=ASOF)
    with pytest.raises(ValueError, match="already happened"):
        run_walk_forward(doc, asof=ASOF)


def test_shipped_example_loads_hashes_and_runs(tmp_path):
    import pathlib

    from dskit.pipeline.document import load_document

    example = (
        pathlib.Path(__file__).parents[2]
        / "examples"
        / "pipeline"
        / "walk-forward.json"
    )
    doc = load_document(str(example))
    assert doc.walkforward is not None
    assert doc.walkforward.fold_cutoffs() == (
        "1973-03-01",
        "1973-03-31",
        "1973-04-30",
    )
    assert doc.hash == load_document(str(example)).hash
    pytest.importorskip("torch")
    obj = json.loads(example.read_text(encoding="utf-8"))
    obj["outputs"]["run_root"] = str(tmp_path / "runs")
    result = run_walk_forward(PipelineDocument.from_obj(obj), asof="1973-08-01")
    assert result.state == "ran"
    assert result.aggregate["n_scored"] == 3
    resolved = read_json(result.folds[0]["run_dir"], "resolved.json")
    assert "val_start_ms" in resolved["splits"]  # the embargo band materialized


def test_cli_verb_prints_the_summary_and_reports_exit(tmp_path, capsys):
    from dskit.pipeline.__main__ import main
    from dskit.pipeline.document import save_document

    doc = probe_doc(tmp_path / "runs", wf_spec())
    path = str(tmp_path / "wf.json")
    save_document(doc, path)
    code = main(["walkforward", path, "--asof", ASOF])
    out = capsys.readouterr().out
    assert code == 0
    assert "WALK-FORWARD RAN" in out
    assert "summary dir:" in out
