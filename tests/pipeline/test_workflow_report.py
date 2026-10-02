"""workflow_report: verify against the ledger first, then write md/html/csv."""

import json

import pytest

from dskit.pipeline.workflow import LEDGER_NAME, path_hash
from dskit.pipeline.workflow_report import ReportError, WorkflowReport, main


def _fixture(tmp_path, rows=None):
    data = tmp_path / "folds.jsonl"
    data.write_text("".join(json.dumps(r) + "\n" for r in (rows or [{"fold": 1, "loss": 0.5}, {"fold": 2, "loss": None}])))
    ledger = {"steps": {"cut": {"exit_code": 0, "config_hash": "abc", "outputs": {"folds": path_hash(str(data))}}}}
    (tmp_path / LEDGER_NAME).write_text(json.dumps(ledger))
    return {
        "ledger": str(tmp_path), "output_dir": str(tmp_path / "report"), "title": "T",
        "statements": [{"title": "Sealed.", "text": "Counts only."}],
        "sections": [{"name": "folds", "title": "Folds", "source": str(data), "format": "jsonl",
                      "step": "cut", "output": "folds"}],
    }


def test_writes_report_files_and_marks_gaps(tmp_path):
    spec = _fixture(tmp_path)
    WorkflowReport(spec).write()
    out = tmp_path / "report"
    assert "n/a" in (out / "report.md").read_text()
    assert "<table>" in (out / "report.html").read_text()
    assert (out / "sections" / "folds.csv").read_text().splitlines()[0] == "fold,loss"
    assert "Sealed." in (out / "report.md").read_text()


def test_refuses_on_hash_mismatch_and_writes_nothing(tmp_path):
    spec = _fixture(tmp_path)
    (tmp_path / "folds.jsonl").write_text('{"fold": 9}\n')
    with pytest.raises(ReportError, match="differs from ledger"):
        WorkflowReport(spec).write()
    assert not (tmp_path / "report").exists()


def test_refuses_empty_missing_and_failed_step(tmp_path):
    spec = _fixture(tmp_path)
    (tmp_path / "folds.jsonl").write_text("")
    with pytest.raises(ReportError, match="no rows"):
        WorkflowReport(spec).write()
    (tmp_path / "folds.jsonl").unlink()
    with pytest.raises(ReportError, match="missing"):
        WorkflowReport(spec).write()
    spec = _fixture(tmp_path)
    ledger = json.loads((tmp_path / LEDGER_NAME).read_text())
    ledger["steps"]["cut"]["exit_code"] = 3
    (tmp_path / LEDGER_NAME).write_text(json.dumps(ledger))
    with pytest.raises(ReportError, match="exit code"):
        WorkflowReport(spec).write()


def test_optional_section_may_be_absent_and_spec_is_default_deny(tmp_path):
    spec = _fixture(tmp_path)
    spec["sections"].append({"name": "extra", "title": "E", "source": str(tmp_path / "none.json"),
                             "format": "json", "required": False})
    WorkflowReport(spec).write()
    assert not (tmp_path / "report" / "sections" / "extra.csv").exists()
    with pytest.raises(ReportError, match="unknown spec key"):
        WorkflowReport({**spec, "typo": 1})


def test_cli_exit_codes(tmp_path):
    spec = _fixture(tmp_path)
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec))
    assert main([str(path)], out=lambda _: None) == 0
    (tmp_path / "folds.jsonl").write_text('{"fold": 9}\n')
    assert main([str(path)], out=lambda _: None) == 1


def test_flatten_makes_dotted_columns_and_joins_short_lists(tmp_path):
    rows = [{"s": "A", "h": {"n": 2, "d": {"x": 1}}, "l": ["a", "b"], "big": [1, 2, 3], "o": [{"k": 1}]}]
    spec = _fixture(tmp_path, rows)
    spec["sections"][0]["flatten"] = {"separator": "; ", "max_items": 2}
    WorkflowReport(spec).write()
    lines = (tmp_path / "report" / "sections" / "folds.csv").read_text().splitlines()
    assert lines[0] == "s,h.n,h.d.x,l,big,o"
    assert lines[1] == 'A,2,1,a; b,"[1,2,3]","[{""k"":1}]"'


def test_flatten_is_off_without_the_key_and_default_deny(tmp_path):
    spec = _fixture(tmp_path, [{"h": {"n": 2}}])
    WorkflowReport(spec).write()
    assert "{" in (tmp_path / "report" / "sections" / "folds.csv").read_text()
    spec["sections"][0]["flatten"] = {"typo": 1}
    with pytest.raises(ReportError, match="flatten"):
        WorkflowReport(spec)


def test_caption_is_shown_beside_the_section(tmp_path):
    spec = _fixture(tmp_path)
    spec["sections"][0]["caption"] = "Counts differ from the nominal window."
    WorkflowReport(spec).write()
    assert "Counts differ" in (tmp_path / "report" / "report.md").read_text()
    assert "Counts differ" in (tmp_path / "report" / "report.html").read_text()


def test_provenance_keeps_only_the_reports_own_lane(tmp_path):
    spec = _fixture(tmp_path)
    ledger = json.loads((tmp_path / LEDGER_NAME).read_text())
    ledger["steps"]["cut"]["who"] = "A"
    ledger["steps"]["other"] = {"exit_code": 0, "who": "B", "outputs": {}}
    (tmp_path / LEDGER_NAME).write_text(json.dumps(ledger))
    spec["required_steps"] = ["cut"]
    WorkflowReport(spec).write()
    assert "other" in (tmp_path / "report" / "report.md").read_text()
    spec["output_dir"] = str(tmp_path / "lane")
    spec["lane"] = {"field": "who", "value": "A"}
    WorkflowReport(spec).write()
    text = (tmp_path / "lane" / "report.md").read_text()
    assert "| cut |" in text and "| other |" not in text
    with pytest.raises(ReportError, match="lane"):
        WorkflowReport({**spec, "lane": {"field": "who"}})
