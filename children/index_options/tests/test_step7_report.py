"""Step 7 (evaluate) and the final report: manifest wiring, spec expansion, report build.

Expected names (stage words, sections, file names) are restated here on purpose: an
assertion sourced from the manifest would assert nothing about it.
"""

import copy
import json
from pathlib import Path

from dskit.pipeline import workflow as wf
from dskit.pipeline import workflow_report as wr
from dskit.pipeline.libs.predictive_cdf import ChronologicalCDFStudy

CHILD = Path(__file__).resolve().parents[1]


def _counts():
    band = {"composite": 0.5, "n": 10, "eligible_n": 9, "dates": 5, "scope": "x",
            "terms": {"nll": {"mean": 0.4, "n": 9, "weight": 0.1},
                      "wing_twcrps": {"mean": 0.3, "n": 9, "weight": 1.0}},
            "decision_brier": 0.2}
    losses = {"training": band, "calibration": band, "validation": dict(band, composite=0.7)}
    state = {"epochs_run_by_seed": [12], "best_epoch_by_seed": [6], "patience": 6,
             "stopped_by_patience": True, "encoder": {"kind": "mlp"}}
    return [{"group": "A", "fold": 1, "fit": {"n": 3}, "base_seconds": 1.0,
             "m_losses": losses, "m_research_state": state}]


def test_loss_rows_are_flat_one_row_per_group_fold_model():
    rows = ChronologicalCDFStudy._loss_rows(_counts(), "fold")
    assert len(rows) == 1
    row = rows[0]
    assert (row["group"], row["fold"], row["model"]) == ("A", 1, "m")
    assert row["training_composite"] == 0.5 and row["validation_composite"] == 0.7
    assert row["validation_nll"] == 0.4 and row["training_wing_twcrps"] == 0.3
    assert row["validation_eligible_n"] == 9 and "training_scope" not in row
    assert row["epochs_run_by_seed"] == [12] and row["stopped_by_patience"] is True
    assert "encoder" not in row and "fit" not in row


def test_loss_rows_skip_models_without_losses():
    counts = [{"group": "A", "fold": 1, "base_fit": {"n": 3}, "base_seconds": 1.0}]
    assert ChronologicalCDFStudy._loss_rows(counts, "fold") == []


# ---------------------------------------------------------------- manifest wiring

CONFIGS = CHILD / "configs"
MANIFEST = json.loads((CONFIGS / "workflow.json").read_text(encoding="utf-8"))
FIXTURE = json.loads((CHILD / "tests" / "fixtures" / "args-report.json").read_text(encoding="utf-8"))
TEMPLATE = json.loads((CONFIGS / "templates" / "report-spec.json").read_text(encoding="utf-8"))
WORK = "@WORK@"
# Restated on purpose: the sections the report must show, per the owner's list.
SECTIONS = [
    "data_points_total", "folds", "data_points_development", "data_points_scored",
    "features_rounds", "features_final", "features_by_family", "features_zoo", "features_hpo",
    "losses_development", "losses_scored", "loss_metrics", "comparisons", "acceptance",
    "identity", "snapshots"]
OPTIONAL = {"features_by_family", "acceptance"}


def _problems():
    try:
        wf.validate_manifest(copy.deepcopy(MANIFEST), str(CONFIGS))
    except wf.WorkflowError as err:
        return err.problems
    return []


def test_step7_mirrors_step6_inputs_except_the_stage_list():
    six, seven = MANIFEST["steps"]["step6"]["in"], MANIFEST["steps"]["step7"]["in"]
    assert {k: v for k, v in six.items() if k != "sequence"} == {
        k: v for k, v in seven.items() if k != "stages"}
    assert seven["stages"] == "$args.evaluate.stages"


def test_step7_reuses_the_step6_template_and_command_takes_the_stage_list():
    flow = wf.Workflow(copy.deepcopy(MANIFEST), str(CONFIGS), None, "QQQ")
    assert flow.entry("step7")["template"] == flow.entry("step6")["template"]
    assert "{in.stages}" in flow.entry("step7")["command"]
    assert "{in.sequence}" in flow.entry("step6")["command"]


def test_step7_outputs_sit_inside_the_shared_study_directory():
    flow = wf.Workflow(copy.deepcopy(MANIFEST), str(CONFIGS), None, "QQQ")
    study = flow.path("step6", "output")
    for out in MANIFEST["steps"]["step7"]["out"]:
        assert flow.path("step7", out).startswith(study + "/")


def test_layout_partition_folders_follow_the_stage_list():
    stages = MANIFEST["args"]["evaluate"]["stages"]["sequence"]
    names = [s["partition"] for s in stages if s["stage"] == "evaluate"]
    layout = MANIFEST["layout"]["step7"]
    assert names == ["development", "later"]
    assert layout["development"].endswith("/evaluate/" + names[0])
    assert layout["scored"].endswith("/evaluate/" + names[1])
    # step 6 already selected; a second select would refuse to overwrite it
    assert "select" not in {s["stage"] for s in stages}


def test_no_problem_is_owned_by_the_report_or_step7_beyond_step6_s_own():
    problems = _problems()
    assert not [p for p in problems if "step report" in p]
    six = {p.replace("step6", "step7") for p in problems if "step step6" in p}
    assert {p for p in problems if "step step7" in p} <= six


def test_every_section_binds_to_a_real_ledger_output_and_a_declared_input():
    steps = MANIFEST["steps"]
    sections = {s["name"]: s for s in TEMPLATE["sections"]}
    assert list(sections) == SECTIONS
    in_map = steps["report"]["in"]
    for section in sections.values():
        step, _, lane = section["step"].partition("@")
        assert lane == "${T}" and section["output"] in steps[step]["out"]
        key = section["source"].split("}")[0][2:]
        assert in_map[key] == f"${step}.out.{section['output']}"
    assert set(TEMPLATE["required_steps"]) == {f"{s}@${{T}}" for s in steps if s != "report"}


def test_every_in_key_and_section_arg_is_used_and_the_args_cover_the_template():
    used = wf.placeholder_names(TEMPLATE)
    assert set(MANIFEST["steps"]["report"]["in"]) - {"W"} <= used | {"L"}
    assert set(MANIFEST["args"]["report"]["sections"]) == set(SECTIONS)
    for name in SECTIONS:
        arg = MANIFEST["args"]["report"]["sections"][name]
        assert {"title", "format", "rows", "required"} <= set(arg)
        assert arg["required"] is (name not in OPTIONAL)


def test_fixture_report_block_equals_the_manifest_args():
    assert FIXTURE["report"]["report"] == MANIFEST["args"]["report"]


def test_the_report_names_the_files_the_study_writes():
    from dskit.pipeline.libs.predictive_cdf import ChronologicalCDFStudy as Study
    files = {name: s.get("file") for name, s in MANIFEST["args"]["report"]["sections"].items()}
    assert files["losses_scored"] == Study.FOLD_LOSSES_FILE
    assert files["data_points_scored"] == "counts.json"
    assert files["loss_metrics"] == files["comparisons"] == "comparison.json"


# ---------------------------------------------------------------- a report built from a tiny run

def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _lines(*rows):
    return "".join(json.dumps(r) + "\n" for r in rows)


def _stage_outputs(work):
    _write(work / "step3/admission.jsonl", _lines({"symbol": "QQQ", "dev_dates": 90, "holdout": {"holdout_dates": 20}}))
    _write(work / "step3/folds.jsonl", _lines({"fold": 1, "role": "warmup"}, {"fold": 2, "role": "scored"}))
    _write(work / "step4/rounds.jsonl", _lines({"round": 1, "family": "f1", "n_features": 4}))
    _write(work / "step4/core.json", json.dumps({"families": ["f1"], "names": ["a", "b"], "by_family": {"f1": 2}}))
    _write(work / "step5/rounds.jsonl", _lines({"candidate": "mlp", "n_features": 2}))
    _write(work / "step6/rounds.jsonl", _lines({"candidate": "mlp_h16", "n_features": 2}))
    for part in ("development", "later"):
        base = work / "study/evaluate" / part
        _write(base / "counts.json", json.dumps([{"group": "QQQ", "fold": 1, "fit": {"n": 5}}]))
        _write(base / "fold_losses.csv", "group,fold,model,training_composite,validation_composite\nQQQ,1,m,0.5,0.7\n")
        _write(base / "complete.json", json.dumps({"identity": {"config": "abc", "panel": "def"}}))
        _write(base / "data_provenance.json", json.dumps({"store": {"snapshot": "s1"}}))
    comparison = {"metrics": [{"model": "m", "metric": "wing_twcrps", "mean": 0.3}],
                  "paired_block_intervals": [{"model": "m", "reference": "horizon_empirical", "lo": 0.1, "hi": 0.9}],
                  "decision_acceptance": {"m": {"passed": True}}}
    _write(work / "study/report/comparison.json", json.dumps(comparison))


def _built(tmp_path, drop=None):
    """Return (spec path, work dir) for a synthetic lane: outputs, ledger and the expanded spec."""
    work = tmp_path / "work"
    _stage_outputs(work)
    text = json.dumps(FIXTURE["report"]).replace(WORK, str(work))
    values = json.loads(text)
    spec = wf.expand(TEMPLATE, values)
    flow_outputs = {
        "step3@QQQ": {"admission": work / "step3/admission.jsonl", "fold_table": work / "step3/folds.jsonl"},
        "step4@QQQ": {"rounds": work / "step4/rounds.jsonl", "core": work / "step4/core.json"},
        "step5@QQQ": {"rounds": work / "step5/rounds.jsonl"},
        "step6@QQQ": {"rounds": work / "step6/rounds.jsonl"},
        "step7@QQQ": {"development": work / "study/evaluate/development",
                      "scored": work / "study/evaluate/later", "report": work / "study/report"}}
    steps = {key: {"exit_code": 0, "lane": "QQQ", "config_hash": "c", "outputs": {k: wf.path_hash(str(v)) for k, v in outs.items()}}
             for key, outs in flow_outputs.items()}
    for name in ("step1", "step1b", "step2"):
        steps[f"{name}@QQQ"] = {"exit_code": 0, "lane": "QQQ", "config_hash": "c", "outputs": {}}
    steps["step1@IWM"] = {"exit_code": 0, "lane": "IWM", "config_hash": "c", "outputs": {}}
    (work / wf.LEDGER_NAME).write_text(json.dumps({"steps": steps}), encoding="utf-8")
    if drop:
        Path(drop.replace(WORK, str(work))).unlink()
    return _write(tmp_path / "spec.json", json.dumps(spec)), work


def test_report_builds_every_section_per_lane(tmp_path):
    spec, work = _built(tmp_path)
    assert wr.main([str(spec)], out=lambda line: None) == 0
    out = work / "out"
    text = (out / "report.md").read_text(encoding="utf-8")
    assert (out / "report.html").is_file()
    for name in SECTIONS:
        title = MANIFEST["args"]["report"]["sections"][name]["title"]
        assert f"## {title}" in text, name
    written = {p.stem for p in (out / "sections").glob("*.csv")}
    assert written == set(SECTIONS)
    assert "Provenance (workflow ledger)" in text and "step7@QQQ" in text
    assert "step1@IWM" not in text
    assert "validation_composite" in (out / "sections/losses_scored.csv").read_text()


def test_missing_source_refuses_and_writes_nothing(tmp_path):
    spec, work = _built(tmp_path, drop=f"{WORK}/study/evaluate/later/fold_losses.csv")
    lines = []
    assert wr.main([str(spec)], out=lines.append) == 1
    assert any("losses_scored" in line and "missing" in line for line in lines)
    assert not (work / "out").exists()


def test_changed_output_after_the_ledger_refuses(tmp_path):
    spec, work = _built(tmp_path)
    (work / "study/report/comparison.json").write_text(json.dumps({"metrics": [{"a": 1}]}))
    lines = []
    assert wr.main([str(spec)], out=lines.append) == 1
    assert any("differs from ledger" in line for line in lines)
    assert not (work / "out").exists()


def test_optional_section_is_dropped_when_its_rows_are_absent(tmp_path):
    spec, work = _built(tmp_path)
    (work / "step4/core.json").write_text(json.dumps({"names": ["a"]}))
    ledger = json.loads((work / wf.LEDGER_NAME).read_text())
    ledger["steps"]["step4@QQQ"]["outputs"]["core"] = wf.path_hash(str(work / "step4/core.json"))
    (work / wf.LEDGER_NAME).write_text(json.dumps(ledger))
    assert wr.main([str(spec)], out=lambda line: None) == 0
    assert not (work / "out/sections/features_by_family.csv").exists()
    assert (work / "out/sections/features_final.csv").is_file()


def test_report_presentation_options_come_from_args_report():
    sections = MANIFEST["args"]["report"]["sections"]
    assert TEMPLATE["lane"] == {"field": "${report.lane_field}", "value": "${T}"}
    by_name = {s["name"]: s for s in TEMPLATE["sections"]}
    for name in ("folds", "data_points_development", "data_points_scored"):
        assert by_name[name]["caption"] == f"${{report.sections.{name}.caption}}"
        assert sections[name]["caption"]
    assert by_name["data_points_total"]["flatten"] == "${report.sections.data_points_total.flatten}"
    assert set(sections["data_points_total"]["flatten"]) == {"separator", "max_items"}
