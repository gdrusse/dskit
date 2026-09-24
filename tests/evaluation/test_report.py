"""The report end to end: five files, self-contained HTML, CLI and the pipeline doorway."""

import csv
import io
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

import dskit
from dskit.evaluation.events import EventLog
from dskit.evaluation.nodes import EvaluationReport
from dskit.evaluation.report import FILENAMES, BacktestReport
from dskit.evaluation.sections import (
    DECISION_COLUMNS,
    DEFAULT_SECTIONS,
    DecisionLogSection,
    OptimizerSection,
    Section,
)
from dskit.evaluation.statistics import SolveSummary
from dskit.pipeline.base import ConfigError
from dskit.pipeline.document import NodeSpec, OutputsConfig, PipelineDocument
from dskit.pipeline.driver import run_document
from dskit.pipeline.node import Node, NodeContext, node_class_errors
from tests.evaluation.conftest import DAY1, MIN, Builder, build_scenario

REPO = os.path.dirname(os.path.dirname(os.path.abspath(dskit.__file__)))


@pytest.fixture
def report(scenario):
    return BacktestReport(EventLog(scenario))


def test_write_lands_the_five_files(tmp_path, report, scenario):
    paths = report.write(str(tmp_path / "out"))
    assert set(paths) == set(FILENAMES)
    assert all(os.path.isfile(p) for p in paths.values())
    reread = EventLog.read(paths["events"])
    assert [e.to_obj() for e in reread] == scenario


def test_the_html_is_self_contained(report):
    page = report.html()
    assert "http" not in page.lower()
    assert "<script" not in page.lower()
    assert "href=" in page  # the in-page table of contents
    assert not re.search(r"\b(?:src|href)\s*=\s*(?![\"']?#)", page)
    assert "prefers-color-scheme: dark" in page


def test_every_svg_in_the_html_parses(report):
    svgs = re.findall(r"<svg.*?</svg>", report.html(), flags=re.S)
    assert len(svgs) >= 8
    for svg in svgs:
        assert ET.fromstring(svg).tag == "svg"


def test_the_html_has_every_section_and_the_why_tooltips(report):
    page = report.html()
    for section in DEFAULT_SECTIONS:
        assert f'id="{section.anchor}"' in page
    assert "ENTRY buy 10 @ 100.05" in page and "EXIT sell 4 @ 101" in page
    # no declared units: scores at three significant figures, signed edges
    assert ("reason edge_above_threshold; forecast 0.00200 rank 1/2; edge +0.00150; "
            "thr 0.000500") in page
    assert "REFUSAL: max_exposure" in page
    # the skip names no instrument and no chosen name: said, not silently dropped
    assert "1 refusal/skip event(s) name no instrument" in page
    assert "slippage -0 bp" not in page
    assert "Verdict: <b>INCONCLUSIVE</b>" in page
    assert "<details><summary>All 8 decisions" in page


def test_the_summary_card_reads_the_verdict_census_and_reasons(report):
    text = report.summary_markdown()
    assert text.startswith("# Synthetic\n")
    assert "**Verdict: INCONCLUSIVE**" in text
    assert "| daily_sharpe |" in text and "insufficient n" in text
    assert "All accounted for." in text
    assert "| refusal | max_exposure | 1 |" in text
    assert "## Provenance" in text and "deadbeef" in text


def test_the_csvs_hold_every_decision_and_trip(report):
    decisions = list(csv.DictReader(io.StringIO(report.decisions_csv())))
    assert [d["decision_id"] for d in decisions] == [f"d{i}" for i in range(1, 9)]
    assert list(decisions[0]) == list(DECISION_COLUMNS)
    first = decisions[0]
    assert (first["runner_up"], first["fill_price"], first["action"]) == ("BBB", "100.05",
                                                                          "enter")
    assert float(first["slippage_bp"]) == pytest.approx(5.0)
    assert decisions[2]["rejections"] == "refusal:max_exposure"
    trips = list(csv.DictReader(io.StringIO(report.trades_csv())))
    assert [t["pnl"] for t in trips] == ["2.9", "10.0", "4.0"]


def test_rendering_is_deterministic(scenario):
    one, two = BacktestReport(EventLog(scenario)), BacktestReport(EventLog(scenario))
    assert one.html() == two.html() and one.summary_markdown() == two.summary_markdown()


def test_the_panel_cap_names_what_it_cut(scenario):
    page = BacktestReport(EventLog(scenario), max_panels=1).html()
    assert "more instrument-day panel(s) not drawn (limit 1)" in page


def test_outcomes_never_reach_the_decision_log(report):
    assert "realized" not in report.decisions_csv()


def test_a_custom_section_list_renders_only_those(scenario):
    class Count(Section):
        title, anchor = "Count", "count"

        def html(self, context):
            return f"<p>{len(context.log)} events</p>"

    page = BacktestReport(EventLog(scenario), sections=[Count]).html()
    assert "<p>31 events</p>" in page and 'id="summary"' not in page


def test_an_unknown_criterion_statistic_refuses_the_report():
    events = build_scenario(criteria=[{"name": "x", "stat": "sharpe", "op": ">",
                                       "value": 0, "min_n": 1}])
    with pytest.raises(ConfigError, match="unknown statistic"):
        BacktestReport(EventLog(events))


def test_a_log_with_no_trading_still_renders():
    b = Builder()
    b.add("run_start", 0, run_id="empty", tz="UTC")
    b.add("run_end", 1, status="ok")
    report = BacktestReport(EventLog(b.events))
    assert "Verdict: <b>UNJUDGED</b>" in report.html()
    assert report.metrics()["verdict"] == "UNJUDGED"


def _solves_log():
    b = Builder()
    b.add("run_start", 0, run_id="solves", tz="UTC")
    rows = [{"name": "budget", "rows": 1, "binding": 1, "min_slack": 0.0},
            {"name": "cap", "rows": 3, "binding": 0, "min_slack": 2.5}]
    b.add("solve", DAY1, solver="appsi_highs", status="ok", termination="optimal",
          objective=10.0, bound=10.0, gap=0.0, seconds=0.5, variables=4, constraints=4,
          binding=rows, model="mio_h01")
    b.add("solve", DAY1 + MIN, solver="appsi_highs", status="ok", termination="optimal",
          objective=12.0, bound=12.0, gap=0.0, seconds=1.5, variables=4, constraints=4,
          binding=[{**rows[0], "binding": 0, "min_slack": 0.25}, rows[1]])
    b.add("solve", DAY1 + 2 * MIN, solver="appsi_highs", status="aborted",
          termination="maxTimeLimit", objective=None, gap=None, seconds=3.0)
    b.add("run_end", DAY1 + 2 * MIN, status="ok")
    return EventLog(b.events)


def test_the_solve_summary_counts_measures_and_binding_frequency():
    summary = SolveSummary(_solves_log())
    assert len(summary) == 3
    assert summary.outcomes() == [
        {"status": "ok", "termination": "optimal", "solves": 2},
        {"status": "aborted", "termination": "maxTimeLimit", "solves": 1},
    ]
    measures = {row["measure"]: row for row in summary.measures()}
    assert measures["seconds"] == {"measure": "seconds", "n": 3, "median": 1.5,
                                   "p90": pytest.approx(2.7), "max": 3.0}
    assert measures["objective"]["n"] == 2 and measures["gap"]["max"] == 0.0
    assert summary.constraints() == [
        {"constraint": "budget", "solves": 2, "binding_solves": 1, "binding_rows": 1,
         "min_slack": 0.0},
        {"constraint": "cap", "solves": 2, "binding_solves": 0, "binding_rows": 0,
         "min_slack": 2.5},
    ]
    assert summary.seconds_series() == [(DAY1, 0.5), (DAY1 + MIN, 1.5), (DAY1 + 2 * MIN, 3.0)]


def test_the_optimizer_section_renders_counts_and_a_time_chart():
    report = BacktestReport(_solves_log())
    page = report.html()
    assert 'id="optimizer"' in page
    section = page.split('id="optimizer"')[1].split("</section>")[0]
    assert "maxTimeLimit" in section and "budget" in section
    assert section.count("<svg") == 1  # seconds over time: three solves
    assert "Optimizer: 3 solves (ok/optimal 2, aborted/maxTimeLimit 1)" in (
        report.summary_markdown())


def test_an_empty_log_says_no_solves_were_recorded(report):
    page = report.html()
    section = page.split('id="optimizer"')[1].split("</section>")[0]
    assert "No optimizer solves recorded." in section and "<svg" not in section
    assert "No optimizer solves recorded." in report.summary_markdown()
    assert OptimizerSection in DEFAULT_SECTIONS
    assert DEFAULT_SECTIONS.index(OptimizerSection) == (
        DEFAULT_SECTIONS.index(DecisionLogSection) + 1)


def test_a_week_of_minutes_stays_small(tmp_path):
    b = Builder()
    b.add("run_start", 0, run_id="week", tz="America/New_York")
    b.add("cashflow", DAY1, amount=10_000)
    for i in range(5 * 24 * 60):
        b.add("mark", DAY1 + i * MIN, "AAA", price=100.0 + (i % 17) * 0.05)
    size = len(BacktestReport(EventLog(b.events), max_panels=10).html())
    assert size < 1_500_000


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli(*args):
    env = {**os.environ, "PYTHONPATH": REPO}
    return subprocess.run([sys.executable, "-m", "dskit.evaluation", *args],
                          capture_output=True, text=True, env=env, cwd=REPO)


def test_the_cli_renders_from_an_events_file(tmp_path, scenario):
    source = tmp_path / "in.jsonl"
    EventLog(scenario).write(str(source))
    done = _cli("render", str(source), "--out", str(tmp_path / "rendered"))
    assert done.returncode == 0, done.stderr
    assert "verdict INCONCLUSIVE" in done.stdout
    for name in FILENAMES.values():
        assert (tmp_path / "rendered" / name).is_file()
    assert (tmp_path / "rendered" / "events.jsonl").read_bytes() == source.read_bytes()


def test_the_cli_refuses_an_invalid_log(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"kind": "mark"}\n', encoding="utf-8")
    done = _cli("render", str(bad), "--out", str(tmp_path / "o"))
    assert done.returncode == 1
    assert "invalid event log" in done.stderr


# ---------------------------------------------------------------------------
# The node doorway
# ---------------------------------------------------------------------------


class ScenarioEvents(Node):
    """A data-role test source that emits the scenario's event dicts."""

    role = "data"
    outputs = ("events",)

    def run(self, ctx, inputs):
        return {"events": build_scenario()}


def test_the_node_class_is_well_formed():
    assert node_class_errors(EvaluationReport, "report") == []


@pytest.mark.parametrize("params, needle", [
    ({}, "out_dir must be"),
    ({"out_dir": "x", "colour": "red"}, "unknown param"),
    ({"out_dir": "x", "title": 3}, "title must be"),
])
def test_node_params_are_default_deny(params, needle):
    with pytest.raises(ConfigError, match=needle):
        EvaluationReport("report", params)


def test_node_inputs_are_checked():
    node = EvaluationReport("report", {"out_dir": "x"})
    assert node.validate_inputs({"events": "nope"})
    assert node.validate_inputs({"events": [1]}) == ["events[0] is not an object (1 such)"]
    assert node.validate_inputs({"events": []}) == []


def test_node_run_writes_under_the_run_dir(tmp_path, scenario):
    node = EvaluationReport("report", {"out_dir": "evaluation", "title": "Doorway"})
    ctx = NodeContext(name="t", asof="2026-09-02", run_dir=str(tmp_path))
    out = node.run(ctx, {"events": scenario})
    assert node.validate_outputs(out) == []
    assert out["paths"]["html"] == str(tmp_path / "evaluation" / "report.html")
    assert out["metrics"]["verdict"] == "INCONCLUSIVE"
    assert out["metrics"]["net_pnl"] == pytest.approx(16.9)


def test_a_document_wires_the_node_by_dotted_path(tmp_path):
    document = PipelineDocument(
        name="evaluation-doorway",
        pipeline={
            "events": NodeSpec(uses="tests.evaluation.test_report:ScenarioEvents"),
            "report": NodeSpec(uses="dskit.evaluation.nodes:EvaluationReport",
                               inputs={"events": "$events.events"},
                               params={"out_dir": "evaluation"}),
        },
        outputs=OutputsConfig(run_root=str(tmp_path)),
    )
    result = run_document(document, asof="2026-09-02", journal=False)
    assert result.state == "ran", result
    assert os.path.isfile(result.outputs["report"]["paths"]["html"])
    assert result.outputs["report"]["metrics"]["trades"] == 3


# --- ADR-0183 phase 2: ledger guard findings in the decision log ---------------


def _with_findings():
    from tests.evaluation.conftest import build_scenario

    events = build_scenario()
    o1 = next(e for e in events if e.get("order_id") == "o1")
    o1["findings"] = [
        {"guard": "size", "measure": "quantity", "value": 10.0, "bound": 100.0,
         "verdict": "allow", "reason": "quantity 10 within 100", "window": "none",
         "scope_key": "AAA"},
        {"guard": "cap", "measure": "notional", "value": 1000.5, "bound": 900.0,
         "verdict": "warn", "reason": "notional over soft cap"},
    ]
    o2 = next(e for e in events if e.get("order_id") == "o2")
    o2["findings"] = [{"guard": "size", "measure": "quantity", "value": 4.0, "bound": 100.0,
                       "verdict": "allow"}]
    return events


def test_findings_reach_the_decision_row_with_the_worst_verdict():
    from dskit.evaluation.report import BacktestReport
    from dskit.evaluation.sections import DecisionLogSection

    report = BacktestReport(EventLog(_with_findings()))
    rows = {r["decision_id"]: r for r in DecisionLogSection().rows(report.context)}
    assert rows["d1"]["guard_verdict"] == "warn"
    assert rows["d1"]["findings"] == ("size quantity 10.0/100 allow; "
                                      "cap notional 1,000/900 warn")
    assert rows["d2"]["guard_verdict"] == "allow"
    assert rows["d6"]["guard_verdict"] is None and rows["d6"]["findings"] is None
    header = next(csv.reader(io.StringIO(report.decisions_csv())))
    assert header[-2:] == ["guard_verdict", "findings"]


def test_findings_summary_groups_by_guard_measure_verdict():
    from dskit.evaluation.report import BacktestReport
    from dskit.evaluation.sections import DecisionLogSection

    log = EventLog(_with_findings())
    summary = DecisionLogSection.findings_summary(log)
    assert summary[0] == {"guard": "size", "measure": "quantity", "verdict": "allow",
                          "count": 2, "min_value": 4.0, "max_value": 10.0, "bound": 100.0}
    assert "Guard findings" in BacktestReport(log).html()
    from tests.evaluation.conftest import build_scenario

    plain = BacktestReport(EventLog(build_scenario()))
    assert "No guard findings" in DecisionLogSection().html(plain.context)


def test_a_malformed_finding_is_refused():
    from dskit.evaluation.events import EvaluationError

    events = _with_findings()
    o1 = next(e for e in events if e.get("order_id") == "o1")
    o1["findings"][0]["verdict"] = "maybe"
    o1["findings"][1]["extra"] = 1
    del o1["findings"][1]["guard"]
    with pytest.raises(EvaluationError) as err:
        EventLog(events)
    text = str(err.value)
    assert "findings[0].verdict" in text and "unknown field(s) ['extra']" in text
    assert "missing required field 'guard'" in text
