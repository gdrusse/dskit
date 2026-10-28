"""The hold report end to end, in its SHIPPED shape, plus the weekly script's shape.

The onboarding root, the decision run and the publication of its decisions stream are
test_hold_vs_redeploy.py's own fixture and helpers, imported rather than copied: the
report reads exactly what the scoring document reads. The report must restate the
decision run's summary, number for number, and render it as the hold verdict page.
"""

import os
import stat
import subprocess

import pytest
from dskit.pipeline import run_document
from dskit.pipeline.document import load_document
from test_hold_vs_redeploy import (  # noqa: F401  (root is a fixture)
    DECIDED, RUN_DOC, _acquire_decisions, _decide, _load, root,
)

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT_DOC = os.path.join(CHILD_ROOT, "configs", "run-hold-report.json")
WEEKLY = os.path.join(CHILD_ROOT, "weekly.sh")


def test_the_report_restates_the_decision_runs_summary_terms():
    """The summary is recomputed from the decided rows, so its terms must be the decision's."""
    report, decide = load_document(REPORT_DOC), load_document(RUN_DOC)
    assert report.pipeline["summary"].params == decide.pipeline["summary"].params
    assert report.pipeline["verdict"].inputs == {"records": "$summary.records",
                                                 "portfolio": "$summary.metrics"}
    assert report.pipeline["decisions"].params["key_fields"] == ["account", "symbol", "as_of"]
    assert report.hash


def test_hold_report_end_to_end(root, monkeypatch):  # noqa: F811  (the imported fixture)
    monkeypatch.chdir(root)
    (root / "report").mkdir()
    decided = _decide(root)
    run_name = os.path.basename(decided.run_dir)
    _acquire_decisions(root, run_name)
    document = _load(REPORT_DOC, str(root), decisions={"stream": run_name})
    result = run_document(document, asof=DECIDED, journal=False)
    assert result.state == "ran", (result.state, result.error)
    # the recomputed summary is the decision run's, number for number (betas aside: the decided
    # rows do not carry them, so the report's beta column is null)
    ours = result.outputs["summary"]
    theirs = decided.outputs["summary"]
    assert [{**row, "beta": None} for row in theirs["records"]] == ours["records"]
    assert {**theirs["metrics"], "beta": None} == ours["metrics"]
    written = result.outputs["verdict"]["path"]
    assert os.path.dirname(os.path.abspath(written)) == str(root / "report")
    assert os.path.basename(written) == os.path.basename(result.run_dir) + ".md"
    with open(written, encoding="utf-8") as fh:
        text = fh.read()
    title = document.pipeline["verdict"].params["title"]
    assert text.startswith(f"# {title} — {DECIDED}\n")
    holdings = [line.split(" | ")[0][2:] for line in text.splitlines()
                if line.startswith("| F00D")]
    rows = sorted(ours["records"], key=lambda r: -r["opportunity_cost"])
    assert holdings == [f"{r['account']} / {r['symbol']}" for r in rows]
    vti = next(line for line in text.splitlines() if " / VTI |" in line)
    assert vti.endswith(" | EXIT |"), "the hurdle clears VTI's break-even (see the decision e2e)"
    assert f"- Total value: {ours['metrics']['total_value']:,.2f}\n" in text
    assert "- Verdicts: " in text and text.rstrip().endswith("what leaving costs today.")
    assert result.outputs["verdict"]["metrics"]["rows"] == len(rows)


def test_weekly_script_is_executable_and_parses():
    assert os.stat(WEEKLY).st_mode & stat.S_IXUSR
    done = subprocess.run(["bash", "-n", WEEKLY], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("token", ["set -euo pipefail", "run-hold-vs-redeploy.json",
                                   "run-hold-report.json", "--source decisions"])
def test_weekly_script_runs_the_shipped_documents(token):
    with open(WEEKLY, encoding="utf-8") as fh:
        assert token in fh.read()
