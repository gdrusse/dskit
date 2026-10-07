"""workflow_rollup: one row per lane from the lanes' study outputs (small real fixtures)."""

import copy
import csv
import json
import os
import random
import shutil

import pytest

from dskit.pipeline.workflow import path_hash
from dskit.pipeline.workflow_rollup import RollupError, WorkflowRollup, main

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "rollup", "QQQ")
CMP = "step6/study_{l}/report/comparison.json"
BEST = "step6/best_{l}.json"
WINNER = "step5/winner_{l}.json"
SECTIONS = "report/report_{l}/sections/"
HOSTILE = '<b>&"'


def _copy_lane(work, lane):
    for root, _, files in os.walk(FIXTURE):
        for name in files:
            rel = os.path.relpath(os.path.join(root, name), FIXTURE)
            target = os.path.join(work, rel.replace("QQQ", lane))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy(os.path.join(root, name), target)


def _edit(work, lane, fn):
    path = os.path.join(work, CMP.format(l=lane))
    doc = json.load(open(path))
    fn(doc["paired_block_intervals"])
    json.dump(doc, open(path, "w"))


def _set_chosen(rows, **fields):
    for row in rows:
        if row["metric"] == "crps" and row["model"] == "mlp_h32x16_lr0.001":
            row.update(fields)


@pytest.fixture
def spec(tmp_path):
    lanes = []
    for lane in ("QQQ", "IWM", "UP"):
        work = str(tmp_path / lane)
        _copy_lane(work, lane)
        lanes.append({"lane": lane, "status": "ok", "work_dir": work, "halting_step": None, "exit_code": 0})
    _edit(str(tmp_path / "UP"), "UP", lambda rows: _set_chosen(rows, lo=0.5, point=1.5))
    lanes.append({"lane": "BAD", "status": "failed", "work_dir": str(tmp_path / "BAD"),
                  "halting_step": "step4@BAD", "exit_code": 5})
    (tmp_path / "batch.json").write_text(json.dumps({"lanes": lanes}))
    sec = "{work_dir}/" + SECTIONS.replace("{l}", "{lane}")
    total = {"path": sec + "data_points_total.csv", "format": "csv"}
    return {
        "batch": str(tmp_path / "batch.json"), "output_dir": str(tmp_path / "out"),
        "title": "Rollup", "min_lower_bound": 0.0, "blocks": [30, 60],
        "intervals": {
            "source": {"path": "{work_dir}/" + CMP.replace("{l}", "{lane}"), "format": "json", "rows": "paired_block_intervals"},
            "where": {"metric": "crps"}, "reference": "horizon_empirical",
            "model": {"source": {"path": "{work_dir}/" + BEST.replace("{l}", "{lane}"), "format": "json"},
                      "field": "value", "where": {"key": "name"}},
            "fields": {"model": "model", "reference": "reference", "block": "block_dates",
                       "lo": "lo", "hi": "hi", "point": "point"},
            "columns": {"skill": "crps_skill_{block}", "lo": "crps_lo_{block}",
                        "hi": "crps_hi_{block}", "model": "model"},
        },
        "columns": [
            {"name": "dev_dates", "kind": "cell", "field": "dev_dates", "source": total},
            {"name": "holdout_dates", "kind": "cell", "field": "holdout.holdout_dates", "source": total},
            {"name": "folds", "kind": "cell", "field": "plan.folds", "source": total},
            {"name": "scored", "kind": "cell", "field": "plan.scored", "source": total},
            {"name": "features", "kind": "count",
             "source": {"path": sec + "features_final.csv", "format": "csv"}},
            {"name": "winner", "kind": "cell", "field": "value", "where": {"key": "winner"},
             "source": {"path": "{work_dir}/" + WINNER.replace("{l}", "{lane}"), "format": "json"}},
        ],
    }


def _rows(spec):
    WorkflowRollup(spec).write()
    with open(os.path.join(spec["output_dir"], "rollup.csv"), newline="") as handle:
        return {r["lane"]: r for r in csv.DictReader(handle)}


def test_row_values_come_from_the_real_outputs(spec):
    qqq = _rows(spec)["QQQ"]
    assert qqq["status"] == "ok" and qqq["winner"] == "mlp"
    assert qqq["dev_dates"] == "1198" and qqq["holdout_dates"] == "301"
    assert qqq["folds"] == "16" and qqq["scored"] == "12" and qqq["features"] == "58"
    assert qqq["model"] == "mlp_h32x16_lr0.001"
    assert float(qqq["crps_lo_30"]) == pytest.approx(-7.488804394520347)
    assert float(qqq["crps_hi_60"]) == pytest.approx(1.741362180607934)
    assert float(qqq["crps_skill_30"]) == pytest.approx(-2.953342191145447)


def test_chosen_model_comes_from_the_winner_file_not_row_order(spec, tmp_path):
    rows = json.load(open(tmp_path / "UP" / CMP.format(l="UP")))["paired_block_intervals"]
    assert len({r["model"] for r in rows if r["metric"] == "crps"}) == 3 and len(rows) == 36
    # another model's rows (which pass the reference/where filter) must never be picked

    def shuffle(rs):
        for r in rs:
            if r["model"] == "option_proxy_transport" and r["metric"] == "crps":
                r["lo"] = 9.0
        random.Random(3).shuffle(rs)

    _edit(str(tmp_path / "UP"), "UP", shuffle)
    up = _rows(spec)["UP"]
    assert up["model"] == "mlp_h32x16_lr0.001" and up["verdict"] == "accepted"
    best = tmp_path / "UP" / BEST.format(l="UP")
    doc = json.load(open(best))
    doc["name"] = "option_proxy_transport"
    json.dump(doc, open(best, "w"))
    assert _rows(spec)["UP"]["model"] == "option_proxy_transport"


def test_verdict_needs_every_block_lower_bound_above_threshold(spec):
    rows = _rows(spec)
    assert rows["QQQ"]["verdict"] == "rejected" and rows["UP"]["verdict"] == "accepted"
    spec["min_lower_bound"] = 0.9
    assert _rows(spec)["UP"]["verdict"] == "rejected"
    spec["min_lower_bound"] = 0.0
    spec["blocks"] = [30, 60, 90]
    assert _rows(spec)["UP"]["verdict"] == "unscored"


def test_one_block_below_threshold_rejects(spec, tmp_path):
    _edit(str(tmp_path / "UP"), "UP", lambda rs: [r.update(lo=-0.1) for r in rs
          if r["model"] == "mlp_h32x16_lr0.001" and r["metric"] == "crps" and r["block_dates"] == 60])
    assert _rows(spec)["UP"]["verdict"] == "rejected"


def test_nan_lower_bound_is_unscored(spec, tmp_path):
    path = tmp_path / "UP" / CMP.format(l="UP")
    path.write_text(path.read_text().replace('"lo": 0.5', '"lo": NaN'))
    assert _rows(spec)["UP"]["verdict"] == "unscored"


def test_duplicate_interval_rows_are_ambiguous_not_picked(spec, tmp_path):
    _edit(str(tmp_path / "UP"), "UP", lambda rs: rs.append(copy.deepcopy(
        next(r for r in rs if r["model"] == "mlp_h32x16_lr0.001" and r["metric"] == "crps"))))
    up = _rows(spec)["UP"]
    assert up["verdict"] == "unscored" and "rows for mlp_h32x16_lr0.001" in up["failures"]


def test_ok_lane_missing_a_declared_column_is_unscored(spec, tmp_path):
    os.remove(tmp_path / "UP" / WINNER.format(l="UP"))
    up = _rows(spec)["UP"]
    assert up["verdict"] == "unscored" and up["winner"] == "" and "winner" in up["failures"]


def test_failed_lane_is_listed_with_its_failure(spec):
    bad = _rows(spec)["BAD"]
    assert bad["verdict"] == "failed" and bad["dev_dates"] == ""
    assert "step4@BAD" in bad["failures"] and "exit 5" in bad["failures"]


def test_skipped_status_is_not_trusted(spec, tmp_path):
    data = json.load(open(spec["batch"]))
    data["lanes"][2]["status"] = "skipped"
    json.dump(data, open(spec["batch"], "w"))
    assert _rows(spec)["UP"]["verdict"] == "failed"


def test_source_hash_is_checked_against_the_ledger(spec, tmp_path):
    work = tmp_path / "UP"
    spec["intervals"]["source"].update(step="s6", output="cmp")
    cmp_path = str(work / CMP.format(l="UP"))
    ledger = {"steps": {"s6@UP": {"exit_code": 0, "outputs": {"cmp": path_hash(cmp_path)}}}}
    (work / "workflow.json").write_text(json.dumps(ledger))
    assert _rows(spec)["UP"]["verdict"] == "accepted"
    ledger["steps"]["s6@UP"]["outputs"]["cmp"] = "0" * 64
    (work / "workflow.json").write_text(json.dumps(ledger))
    up = _rows(spec)["UP"]
    assert up["verdict"] == "unscored" and "hash differs" in up["failures"]


def test_html_is_self_contained_sortable_and_escapes_hostile_lane_names(spec, tmp_path):
    data = json.load(open(spec["batch"]))
    data["lanes"].append({"lane": HOSTILE, "status": "failed", "work_dir": "x",
                          "halting_step": HOSTILE, "exit_code": 1})
    json.dump(data, open(spec["batch"], "w"))
    WorkflowRollup(spec).write()
    page = open(os.path.join(spec["output_dir"], "report.html")).read()
    assert "<table" in page and "sort" in page.lower()
    assert "http://" not in page and "https://" not in page and "src=" not in page
    assert HOSTILE not in page and "&lt;b&gt;&amp;&quot;" in page


def test_csv_neutralizes_formula_text_but_keeps_negative_numbers(spec):
    data = json.load(open(spec["batch"]))
    data["lanes"].append({"lane": "=1+1", "status": "failed", "work_dir": "x",
                          "halting_step": "@x", "exit_code": 1})
    json.dump(data, open(spec["batch"], "w"))
    rows = _rows(spec)
    assert "=1+1" not in rows and "'=1+1" in rows
    assert float(rows["QQQ"]["crps_skill_30"]) < 0
    assert rows["'=1+1"]["failures"].startswith("failed at @x")


@pytest.mark.parametrize("edit, text", [
    (lambda s: s.update(bogus=1), "bogus"),
    (lambda s: s.pop("blocks"), "blocks"),
    (lambda s: s["intervals"].pop("where"), "where"),
    (lambda s: s["intervals"].update(where={}), "where"),
    (lambda s: s["intervals"].pop("model"), "model"),
    (lambda s: s["intervals"]["columns"].update(skill="s_{nope}"), "template"),
    (lambda s: s["intervals"]["columns"].update(skill="s"), "duplicate"),
    (lambda s: s["columns"][0].update(name="lane"), "duplicate"),
    (lambda s: s["columns"][0]["source"].update(path="{work_dir}/{missing}.csv"), "template"),
    (lambda s: s["columns"][0]["source"].update(step="s"), "come together"),
])
def test_spec_refusals(spec, edit, text):
    edit(spec)
    with pytest.raises(RollupError, match=text):
        WorkflowRollup(spec)


def test_malformed_batch_entries_are_refused(spec):
    for bad in ({"lanes": [{"status": "ok"}]}, {"lanes": "x"}, {"lanes": [1]},
                {"lanes": [{"lane": "A"}, {"lane": "A"}]}):
        json.dump(bad, open(spec["batch"], "w"))
        with pytest.raises(RollupError):
            WorkflowRollup(spec).write()


def test_duplicate_csv_headers_and_non_object_jsonl_rows_are_unusable(spec, tmp_path):
    csv_path = tmp_path / "UP" / (SECTIONS.format(l="UP") + "features_final.csv")
    csv_path.write_text("value,value\na,b\n")
    up = _rows(spec)["UP"]
    assert up["verdict"] == "unscored" and "duplicate column headers" in up["failures"]
    spec["columns"][4]["source"] = {"path": "{work_dir}/rows.jsonl", "format": "jsonl"}
    (tmp_path / "QQQ" / "rows.jsonl").write_text('{"a":1}\n[1,2]\n')
    assert "unreadable" in _rows(spec)["QQQ"]["failures"]


def test_cli_main(spec, tmp_path):
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec))
    assert main([str(path)]) == 0
    assert (tmp_path / "out" / "report.html").exists()
    assert main([str(tmp_path / "nope.json")]) == 1
    spec["bogus"] = 1
    path.write_text(json.dumps(spec))
    assert main([str(path)]) == 1


def test_block_without_a_finite_lower_bound_gets_a_note(spec, tmp_path):
    path = tmp_path / "UP" / CMP.format(l="UP")
    path.write_text(path.read_text().replace('"lo": 0.5', '"lo": NaN'))
    up = _rows(spec)["UP"]
    assert up["verdict"] == "unscored" and "no finite lower bound at block 30" in up["failures"]


def test_where_reads_cells_by_the_report_s_table_text_with_the_lane_filled_in():
    # ADR-0236 amendment 2: one matching rule, workflow_report.row_matches, for both modules.
    from dskit.pipeline.workflow_rollup import _matches
    assert _matches({"metric": "crps", "lane": "UP"}, {"metric": "crps", "lane": "{lane}"}, "UP")
    assert not _matches({"metric": "crps"}, {"metric": "{lane}"}, "UP")
    assert _matches({"metric": None}, {"metric": "n/a"}, "UP")       # an empty cell reads n/a
    assert _matches({"block": 30}, {"block": "30"}, "UP") and _matches({}, None, "UP")
