"""Tests for the neutral step 3-6 templates (golden for 4-6; plan and behaviour for 3).

Each template under ``configs/templates`` is a normal document in which every
project-varying value is a ``${name.path}`` placeholder; ``fixtures/args-study.json``
holds the QQQ values, and expanding one with the other must deep-equal the
checked-in ``run-step*.json`` (``notes`` excluded).  The expander is the production one.
"""

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from dskit.pipeline.document import PipelineDocument, load_document
from dskit.pipeline.node import Node
from dskit.pipeline.planner import plan

CONFIGS = Path(__file__).resolve().parents[1] / "configs"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "args-study.json"
STEPS = {
    "step3": "step3-holdout-folds",
    "step4": "step4-feature-selection",
    "step5": "step5-model-zoo",
    "step6": "step6-hpo",
}


def expand(template, args):
    """Expand with the production expander; refuse missing (ValueError) and unused names."""
    from dskit.pipeline.workflow import WorkflowError, expand as production, placeholder_names

    try:
        out = production(template, args)
    except WorkflowError as err:
        raise ValueError(str(err)) from None
    if set(args) - placeholder_names(template):
        raise ValueError(f"unused in keys: {sorted(set(args) - placeholder_names(template))}")
    return out


def strip_notes(node):
    if isinstance(node, dict):
        return {k: strip_notes(v) for k, v in node.items() if k != "notes"}
    if isinstance(node, list):
        return [strip_notes(v) for v in node]
    return node


def _template(step):
    return json.loads((CONFIGS / "templates" / f"{STEPS[step]}.json").read_text())


def _args(step):
    return json.loads(FIXTURE.read_text())[step]


def _checked_in(step):
    return json.loads((CONFIGS / f"run-{STEPS[step]}.json").read_text())


#: Step 3 is structurally generated (one family-availability node), so it has no golden.
GOLDEN_STEPS = [step for step in STEPS if step != "step3"]


@pytest.mark.parametrize("step", GOLDEN_STEPS)
def test_expanding_the_template_with_the_fixture_equals_the_checked_in_config(step):
    assert strip_notes(expand(_template(step), _args(step))) == strip_notes(_checked_in(step))


@pytest.mark.parametrize("step", STEPS)
def test_a_template_carries_no_project_literal_outside_notes(step):
    body = json.dumps(strip_notes(_template(step)))
    for literal in ("QQQ", "VXN", "IWM", "/home/"):
        assert literal not in body, literal


@pytest.mark.parametrize("step", STEPS)
def test_every_fixture_key_is_used_by_its_template(step):
    expand(_template(step), _args(step))        # raises on an unused in key


@pytest.mark.parametrize("step", STEPS)
def test_a_missing_value_refuses(step):
    args = _args(step)
    args.pop("S")
    with pytest.raises(ValueError, match="no value"):
        expand(_template(step), args)


def test_an_unused_in_key_refuses():
    with pytest.raises(ValueError, match="unused"):
        expand({"a": "${x}"}, {"x": 1, "y": 2})


def test_placeholder_grammar():
    args = {"n": 3, "L": [1, 2], "o": {"k": None}, "s": "txt"}
    doc = {"a": "${n}", "b": "${L}", "c": "${o.k}", "d": "v=${n}/${s}", "e": "$$${s}",
           "f": "$each", "g": "$node.port", "notes": "${untouched}", "h": ["${s}", 7]}
    assert expand(doc, args) == {
        "a": 3, "b": [1, 2], "c": None, "d": "v=3/txt", "e": "$txt", "f": "$each",
        "g": "$node.port", "notes": "${untouched}", "h": ["txt", 7]}


SHARED = ("S", "F", "C", "V", "M", "G", "H", "windows", "regions", "resolutions", "bootstrap",
          "acceptance", "features", "references")


def test_shared_study_values_agree_across_steps_4_to_6():
    fixture = json.loads(FIXTURE.read_text())
    steps = [fixture[s] for s in ("step4", "step5", "step6")]
    for name in SHARED:
        assert steps[0][name] == steps[1][name] == steps[2][name], name
    assert steps[0]["selection"]["metric"] == steps[1]["selection"]["metric"] == (
        steps[2]["selection"]["metric"])


def test_step3_schema_names_agree_with_the_studies():
    fixture = json.loads(FIXTURE.read_text())
    for key, value in fixture["step3"]["S"].items():
        assert fixture["step4"]["S"].get(key, value) == value, key


def test_step3_expansion_plans_and_has_no_per_family_nodes(child_root, monkeypatch, tmp_path):
    monkeypatch.chdir(child_root)
    path = tmp_path / "expanded-step3.json"
    path.write_text(json.dumps(expand(_template("step3"), _args("step3"))))
    order = plan(load_document(str(path))).order
    stems = {name.split("__")[0] for name in order}
    assert {"availability", "complete_dates", "training", "plan", "fold_evidence",
            "admission_evidence"} <= stems
    assert not [n for n in order if n.startswith(("has_", "admit_", "required_", "rate_"))]
    assert len(order) < 30


# -- step 3 behaviour: the expanded document run on a small synthetic panel -----------------------
#
# 30 consecutive days, label settles ``LAG`` days later, holdout fraction 0.2 -> last 6 dates
# locked (idx 24-29) and idx 22-23 purged (label reaches idx 24), so dev = idx 0-21 (22 dates).
# family "a" is always available; "b" is missing on idx 5 and 9 (20/22 = .909, admitted at .75);
# "c" is missing on idx 0-6 (15/22 = .68, rejected) but available on every holdout date, so a
# holdout leak would lift it to 23/30 = .77 and wrongly admit it.

LAG = 2
N_DAYS = 30
FAMILY_MISSING = {"a": set(), "b": {5, 9}, "c": set(range(7))}
DEV_IDX = list(range(22))
COMPLETE_IDX = [i for i in DEV_IDX if i not in FAMILY_MISSING["b"]]


class StaticRows(Node):
    """Test double for the two onboarding readers: serves ``params.rows`` as ``records``."""

    role = "data"
    outputs = ("records",)
    _PARAMS = ("rows",)

    @classmethod
    def validate_params(cls, params):
        return [] if "rows" in params else ["rows is required"]

    def run(self, ctx, inputs):
        return {"records": [dict(r) for r in self.params["rows"]]}


def _day(i):
    return (date(2020, 1, 1) + timedelta(days=i)).isoformat()


def _synthetic_args(out):
    args = _args("step3")
    names = sorted(FAMILY_MISSING)
    args["T"] = "ZZZ"
    args["H"] = LAG
    S = args["S"]
    args["families"] = {"flags": {n: {"flag": f"available_{n}"} for n in names},
                        "dates_fields": [S["symbol"], S["quote_date"], *(f"available_{n}" for n in names)]}
    args["split"] = {"tau": 0.75, "holdout_fraction": 0.2,
                     "folds": {"train_n": 4, "val_n": 3, "step_n": 3, "warmup_folds": 1}}
    args["L"] = {"step3": {"fold_table": str(out / "fold-table.jsonl"),
                           "admission": str(out / "admission.jsonl"),
                           "run_root": str(out / "runs")}}
    return args, S


def _synthetic_doc(out):
    args, S = _synthetic_args(out)
    doc = expand(_template("step3"), args)
    dates = [{S["symbol"]: "ZZZ", S["quote_date"]: _day(i),
              **{f"available_{n}": "no" if i in gone else "yes"
                                for n, gone in FAMILY_MISSING.items()}}
             for i in range(N_DAYS)]
    panel = [{S["symbol"]: "ZZZ", S["quote_date"]: _day(i), S["dte"]: LAG, S["expiry"]: _day(i + LAG),
              S["settlement"]: _day(i + LAG), S["target"]: 0.01 * (i % 5)} for i in range(N_DAYS)]
    for name, rows in (("step2_dates", dates), ("source", panel)):
        doc["pipeline"][name] = {"uses": f"{__name__}:StaticRows", "params": {"rows": rows}}
    return doc


def _read_jsonl_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.fixture
def step3_run(tmp_path, monkeypatch):
    from dskit.pipeline.driver import run_document
    out = tmp_path / "out"
    (out).mkdir()
    monkeypatch.chdir(tmp_path)
    result = run_document(PipelineDocument.from_obj(_synthetic_doc(out)), asof="2026-10-02",
                          journal=False)
    assert result.state == "ran", result
    return _read_jsonl_rows(out / "fold-table.jsonl"), _read_jsonl_rows(out / "admission.jsonl")


def test_step3_admission_uses_dev_dates_only(step3_run):
    _, (admission,) = step3_run
    assert admission["holdout"]["holdout_start"] == _day(24)
    assert admission["holdout"]["dev_dates"] == admission["dev_dates"] == len(DEV_IDX)
    assert admission["rate_a"] == 1.0
    assert admission["rate_b"] == pytest.approx(20 / 22)
    assert admission["rate_c"] == pytest.approx(15 / 22)
    assert [admission[f"required_{n}"] for n in "abc"] == [1, 1, 0]
    assert admission["admitted"] == ["a", "b"]


def test_step3_folds_are_sized_by_complete_case_dates_and_end_anchored(step3_run):
    folds, (admission,) = step3_run
    complete = [_day(i) for i in COMPLETE_IDX]
    assert {(f["train_dates"], f["val_dates"]) for f in folds} == {(4, 3)}
    assert folds[-1]["val_end"] == complete[-1] and folds[-1]["val_start"] == complete[-3]
    ends = [f["val_end"] for f in folds]
    assert ends == sorted(ends) and all(e in complete for e in ends)
    assert all(f["val_start"] in complete and f["train_start"] in complete for f in folds)
    assert all(f["val_end"] < admission["holdout"]["holdout_start"] for f in folds)
    assert [f["role"] for f in folds][:1] == ["warmup"] and admission["plan"]["folds"] == len(folds)
    assert admission["plan"]["scored_start"] == folds[1]["val_start"]


def test_step3_dates_missing_an_admitted_family_never_open_a_window(step3_run):
    folds, _ = step3_run
    bad = {_day(i) for i in FAMILY_MISSING["b"]}
    assert not bad & {f[k] for f in folds for k in ("train_start", "train_end", "val_start",
                                                     "val_end")}
    assert {f["symbol"] for f in folds} == {"ZZZ"}


@pytest.mark.parametrize("step", ["step4", "step5", "step6"])
def test_studies_expand_to_a_document_the_hpo_study_accepts(step, child_root, monkeypatch):
    from dskit.pipeline.libs.predictive_cdf import CDFHyperparameterStudy
    doc = expand(_template(step), _args(step))
    pinned = child_root / doc["study"]["fold_table"]["path"]
    if not pinned.exists():
        pytest.skip("the pinned step-3 fold table is not in this checkout")
    monkeypatch.chdir(child_root)
    CDFHyperparameterStudy(doc)


@pytest.mark.parametrize("step", ["step4", "step5", "step6"])
def test_expanded_fold_table_passes_the_study_spec_check(step):
    from dskit.pipeline.libs.predictive_cdf import _FoldPlan
    spec = expand(_template(step), _args(step))["study"]["fold_table"]
    assert set(spec) == {"path", "sha256", "holdout_start", "cal_n", "roles"}
    _FoldPlan.__new__(_FoldPlan)._check_spec(spec)


@pytest.mark.parametrize("step", ["step4", "step5", "step6"])
def test_the_fold_table_arrives_as_a_pin_and_the_fold_use_block(step):
    args = _args(step)
    assert set(args["fold_table"]) == {"path", "sha256", "holdout_start"}
    assert set(args["folds"]) == {"cal_n", "roles"}
    assert "fold_table" not in args["F"]


@pytest.mark.parametrize("step", ["step4", "step5", "step6"])
def test_a_bare_string_fold_table_is_refused_by_the_spec_check(step):
    from dskit.pipeline.libs.predictive_cdf import _FoldPlan
    with pytest.raises(ValueError, match="invalid fold_table"):
        _FoldPlan.__new__(_FoldPlan)._check_spec("pipeline_runs/fold-table.jsonl")


def test_step3_pin_value_path_resolves_in_the_admission_record(step3_run):
    manifest = json.loads((CONFIGS / "workflow.json").read_text())
    spec = manifest["registry"]["step3"]["pins"]["fold_table"]["values"]["holdout_start"]
    _, (admission,) = step3_run
    node = admission
    for part in spec["path"].split("."):
        node = node[part]
    assert node == admission["holdout"]["holdout_start"] == _day(24)


# -- step 4-6 strategies (ADR-0229): the manifest's study vocabulary -----------------------------

MANIFEST = json.loads((CONFIGS / "workflow.json").read_text(encoding="utf-8"))
IO = MANIFEST["args"]["study_io"]
STAGE_LISTS = (MANIFEST["args"]["selection"]["run"], MANIFEST["args"]["zoo_sequence"],
               MANIFEST["args"]["hpo"]["run"], MANIFEST["args"]["evaluate"]["stages"])
STUDY_SOURCE = (Path(__file__).resolve().parents[3] / "dskit" / "pipeline" / "libs"
                / "predictive_cdf.py").read_text(encoding="utf-8")


def test_every_stage_record_field_has_a_command_flag_and_the_wildcard_agrees():
    for block in STAGE_LISTS:
        for record in block["sequence"]:
            assert set(record) <= set(IO["cli"]["flags"]), record
            assert isinstance(record.get(IO["cli"]["expand"], IO["cli"]["wildcard"]), str)
    assert any(record.get(IO["cli"]["expand"]) == IO["cli"]["wildcard"]
               for block in STAGE_LISTS for record in block["sequence"])


def test_the_study_writes_the_files_and_fields_the_collectors_read():
    select = IO["select"]
    assert f'"{select["dir"]}"' in STUDY_SOURCE and f'"{select["file"]}"' in STUDY_SOURCE
    for field in ("candidate", "partition", "score", "winner", "variant"):
        assert f'"{select[field]}"' in STUDY_SOURCE, field
    for dotted in (IO["at"]["output"], IO["at"]["candidates"], IO["cli"]["partitions_at"]):
        assert dotted.split(".")[0] in ("experiment",)
    assert f'"{IO["cli"]["partitions_at"].rsplit(".", 1)[1]}"' in STUDY_SOURCE


def test_candidate_spec_paths_name_keys_the_model_spec_and_study_use():
    assert IO["paths"]["features"].rsplit(".", 1)[1] in STUDY_SOURCE
    params = MANIFEST["args"]["model"]["params"]
    assert IO["paths"]["encoder"].rsplit(".", 1)[1] in params
    for axis, path in MANIFEST["args"]["hpo"]["paths"].items():
        assert path.rsplit(".", 1)[1] in params, axis


def test_the_zoo_sequence_rows_name_features_the_shared_order_holds():
    order = MANIFEST["args"]["feature_order"]["names"]
    for name, encoder in MANIFEST["args"]["zoo"]["candidates"].items():
        rows = encoder.get(IO["paths"]["sequence_names"], [])
        assert {n for row in rows for n in row} <= set(order), name


def test_the_selection_pool_is_declared_in_the_family_availability_block():
    families = MANIFEST["args"]["families"]["availability"]
    assert set(MANIFEST["args"]["selection"]["pool"]) <= set(families)
    assert set(MANIFEST["args"]["core"]["families"]) <= set(families)
    assert MANIFEST["args"]["selection"]["stop"]["rule"] == "no_gain"


# -- stock-lane nulls: acceptance and decision regions are optional keys (ADR-0232) ----------------------

NULLABLE = ("step4", "step5", "step6")


def _expanded(step, **over):
    return expand(_template(step), {**_args(step), **over})


@pytest.mark.parametrize("step", NULLABLE)
def test_null_acceptance_and_regions_leave_their_keys_out_of_the_study_document(step):
    doc = _expanded(step, acceptance=None, regions=None)
    assert "decision_acceptance" not in doc["study"] and "decision_regions" not in doc["data"]
    full = _expanded(step)
    assert "decision_acceptance" in full["study"] and "decision_regions" in full["data"]


@pytest.mark.parametrize("step", NULLABLE)
def test_only_the_two_nulled_keys_differ_from_the_full_document(step):
    full, bare = _expanded(step), _expanded(step, acceptance=None, regions=None)
    del full["study"]["decision_acceptance"], full["data"]["decision_regions"]
    assert strip_notes(full) == strip_notes(bare)


def test_the_study_accepts_a_document_without_acceptance(tmp_path):
    from dskit.pipeline.libs.predictive_cdf import ChronologicalCDFStudy

    study = _expanded("step4", acceptance=None, regions=None)["study"]
    study = {k: v for k, v in study.items() if k != "fold_table"}
    study.update(years=[2020, 2021], development_end=2020)
    assert ChronologicalCDFStudy(study).config["reference_model"] == study["reference_model"]
    with pytest.raises(ValueError, match="acceptance"):
        ChronologicalCDFStudy({**study, "decision_acceptance": {"blocks": [30]}})


@pytest.mark.parametrize("step", NULLABLE)
def test_the_regions_arg_carries_the_archive_root_the_study_data_block_names(step):
    args = _args(step)
    assert args["regions"]["archive_root"] == args["F"]["archive_root"]


def test_the_manifest_regions_arg_names_the_same_archive_root_as_the_feature_sources():
    manifest = json.loads((CONFIGS / "workflow.json").read_text())["args"]
    assert manifest["decision_regions"]["archive_root"] == manifest["feature_sources"]["archive_root"]


#: Expanded-config hashes of the recorded index lanes (notes stripped, keys sorted, plan-mode values, deferred outputs fixed)
#: from before the stock-lane hooks; a change that moves one has changed what QQQ/IWM compute.
RECORDED_LANES = ("QQQ", "IWM")
RECORDED_STEPS = ("step1", "step1b", "step2", "step3", "step4", "step5", "step6", "step7", "report")

RECORDED_HASHES = {
    "IWM/report": "51fca722ee4267a6",
    "IWM/step1": "1da151f3c50bcc45",
    "IWM/step1b": "98e8153484abb94b",
    "IWM/step2": "0cb70a6aea5da783",
    "IWM/step3": "9464736bee71c4f0",
    "IWM/step4": "bba673ce31dd59a1",
    "IWM/step5": "f351f5d612877328",
    "IWM/step6": "3f083c7740961b49",
    "IWM/step7": "3f083c7740961b49",
    "QQQ/report": "e0abef83f6757a6c",
    "QQQ/step1": "45c7481789f15ce7",
    "QQQ/step1b": "fd9e07163fe41595",
    "QQQ/step2": "c73da015e76b2793",
    "QQQ/step3": "b58fa1fa15d1e648",
    "QQQ/step4": "25276399a201594e",
    "QQQ/step5": "42d1cc1d983d1ea4",
    "QQQ/step6": "ddf362de45cf40aa",
    "QQQ/step7": "ddf362de45cf40aa",
}


def _lane_hashes():
    import hashlib
    from dskit.pipeline.workflow import lane_flows

    manifest = json.loads((CONFIGS / "workflow.json").read_text())
    out = {}
    for flow in lane_flows(manifest, str(CONFIGS), None):
        for step in RECORDED_STEPS:
            doc = flow.expanded(step, False, 0)
            text = json.dumps(strip_notes(doc), sort_keys=True, default=lambda _: "DEFERRED")
            out[f"{flow.lane}/{step}"] = hashlib.sha256(text.encode()).hexdigest()[:16]
    return out


def test_the_recorded_lanes_expand_to_the_same_configs_as_before_the_stock_hooks():
    assert _lane_hashes() == RECORDED_HASHES
