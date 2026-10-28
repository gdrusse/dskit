"""The hold report end to end, in its SHIPPED shape, plus the weekly script run for real.

The onboarding root, the decision run and the publication of its decisions stream are
test_hold_vs_redeploy.py's own fixture and helpers, imported rather than copied: the
report reads exactly what the scoring document reads. The report must restate the
decision run's summary, number for number, and render it as the hold verdict page.

``weekly.sh`` runs here against a copy of itself and of the shipped configs beside a
scratch onboarding root (``localfiles`` standing in for Schwab), for a day other than the
shipped placeholder, with a decoy snapshot on the placeholder day carrying other values:
the page, the decisions file and the hand-worked numbers can only be the run day's if the
script substituted the day into the decision document and the decision run's name into the
report document.
"""

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from datetime import date, timedelta

import dskit
import pytest
from dskit.pipeline import run_document
from dskit.pipeline.document import load_document
from test_hold_vs_redeploy import (  # noqa: F401  (root is a fixture)
    ACCOUNT, ACTIVE, DECIDED, LATER_DAY, RUN_DOC, THEN, _acquire_decisions, _cli, _decide,
    _jsonl, _load, _shipped, root,
)

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT_DOC = os.path.join(CHILD_ROOT, "configs", "run-hold-report.json")
WEEKLY = os.path.join(CHILD_ROOT, "weekly.sh")


def test_the_report_restates_the_decision_runs_summary_terms():
    """The summary is recomputed from the decided rows, so its terms must be the decision's."""
    report, decide = load_document(REPORT_DOC), load_document(RUN_DOC)
    ours, theirs = dict(report.pipeline["summary"].params), dict(decide.pipeline["summary"].params)
    # the one difference: as_of is a key field here, so the day rides onto the summary rows
    assert ours.pop("key_fields") == theirs.pop("key_fields") + ["as_of"]
    assert ours == theirs
    assert report.pipeline["verdict"].inputs == {"records": "$summary.records",
                                                 "portfolio": "$summary.metrics"}
    assert report.pipeline["verdict"].params["as_of_field"] == "as_of"
    assert report.pipeline["decisions"].params["key_fields"] == ["account", "symbol", "as_of"]
    assert report.hash


def test_hold_report_end_to_end(root, monkeypatch):  # noqa: F811  (the imported fixture)
    monkeypatch.chdir(root)
    (root / "report").mkdir()
    decided = _decide(root)
    run_name = os.path.basename(decided.run_dir)
    _acquire_decisions(root, run_name)
    document = _load(REPORT_DOC, str(root), decisions={"stream": run_name})
    # a year later: the title is the decisions' own day, never the day the report is run
    result = run_document(document, asof=LATER_DAY, journal=False)
    assert result.state == "ran", (result.state, result.error)
    # the recomputed summary is the decision run's, number for number (betas aside: the decided
    # rows do not carry them, so the report's beta column is null; as_of rides along as a key)
    ours = result.outputs["summary"]
    theirs = decided.outputs["summary"]
    assert {row["as_of"] for row in ours["records"]} == {DECIDED}
    assert [{**row, "beta": None} for row in theirs["records"]] == [
        {k: v for k, v in row.items() if k != "as_of"} for row in ours["records"]]
    assert {**theirs["metrics"], "beta": None} == ours["metrics"]
    written = result.outputs["verdict"]["path"]
    assert os.path.dirname(os.path.abspath(written)) == str(root / "report")
    assert os.path.basename(written) == os.path.basename(result.run_dir) + ".md"
    with open(written, encoding="utf-8") as fh:
        text = fh.read()
    title = document.pipeline["verdict"].params["title"]
    assert text.startswith(f"# {title} — {DECIDED}\n") and LATER_DAY not in text
    holdings = [line.split(" | ")[0][2:] for line in text.splitlines()
                if line.startswith("| F00D")]
    rows = sorted(ours["records"], key=lambda r: -r["opportunity_cost"])
    assert holdings == [f"{r['account']} / {r['symbol']}" for r in rows]
    vti = next(line for line in text.splitlines() if " / VTI |" in line)
    assert vti.endswith(" | EXIT |"), "the hurdle clears VTI's break-even (see the decision e2e)"
    assert f"- Total value: {ours['metrics']['total_value']:,.2f}\n" in text
    assert "- Verdicts: " in text
    assert text.rstrip().endswith("unless loss_offset is declared.")
    assert result.outputs["verdict"]["metrics"]["rows"] == len(rows)


# --------------------------------------------------------------------- weekly.sh, shape

def test_weekly_script_is_executable_and_parses():
    assert os.stat(WEEKLY).st_mode & stat.S_IXUSR
    done = subprocess.run(["bash", "-n", WEEKLY], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("token", ["set -euo pipefail", "run-hold-vs-redeploy.json",
                                   "run-hold-report.json", "--source decisions"])
def test_weekly_script_runs_the_shipped_documents(token):
    with open(WEEKLY, encoding="utf-8") as fh:
        assert token in fh.read()


def test_weekly_script_needs_no_bash_4():
    """macOS ships bash 3.2: no inherit_errexit (4.4), no associative arrays (4), no ``${x,,}``."""
    with open(WEEKLY, encoding="utf-8") as fh:
        code = "\n".join(line for line in fh.read().splitlines()
                         if not line.lstrip().startswith("#"))   # the comments may name them
    for token in ("inherit_errexit", "declare -A", "mapfile", "readarray", ",,}", "^^}"):
        assert token not in code, token


# --------------------------------------------------------------------- weekly.sh, run for real

#: Two snapshot days: the shipped placeholder (a decoy, other values) and the run day.
SHIPPED_DAY = _shipped(RUN_DOC, "tax")["as_of"]
WEEK_DAY = (date.fromisoformat(SHIPPED_DAY) - timedelta(days=7)).isoformat()
#: VTI and AAPL market values on each day; basis 2000 and 450 on both (THEN's).
VALUES = {SHIPPED_DAY: (2500.0, 600.0), WEEK_DAY: (2750.0, 640.0)}


def _snapshot(day, vti, aapl):
    """THEN's four rows (two equity longs, a short, an option) re-dated and re-valued."""
    return [{**THEN[0], "as_of": day, "market_value": vti},
            {**THEN[1], "as_of": day, "market_value": aapl},
            {**THEN[2], "as_of": day}, {**THEN[3], "as_of": day}]


@pytest.fixture
def sandbox(tmp_path):
    """The script and the shipped configs beside a fresh onboarding root.

    ``schwab`` is a registered ``localfiles`` source holding both snapshots, NOT yet acquired
    (the script's own pull does that); the active series is acquired, as the README's one-time
    setup does. 52 weekly returns meet the shipped document's floor of one year.
    """
    assert WEEK_DAY != SHIPPED_DAY
    assert len(ACTIVE) == 12
    shutil.copy(WEEKLY, tmp_path / "weekly.sh")
    shutil.copytree(os.path.join(CHILD_ROOT, "configs"), tmp_path / "configs")
    data, active = tmp_path / "data", tmp_path / "active"
    data.mkdir()
    active.mkdir()
    _jsonl(data / "positions.jsonl",
           [row for day, (vti, aapl) in VALUES.items() for row in _snapshot(day, vti, aapl)])
    start = date(2025, 1, 6)
    _jsonl(active / "returns.jsonl", [
        {"period": f"week-{i:02d}", "net_return": value, "day": (start + timedelta(days=7 * i)).isoformat()}
        for i, value in enumerate((ACTIVE * 5)[:52])])
    _cli(tmp_path, "init", "--root", "./ob")
    _cli(tmp_path, "register-source", "schwab", "--root", "./ob",
         "--catalog-source", "schwab-src", "--connector", "localfiles",
         "--config", json.dumps({"path": str(data), "effective_field": "as_of"}), "--activate")
    _cli(tmp_path, "register-source", "active", "--root", "./ob",
         "--catalog-source", "active-src", "--connector", "localfiles",
         "--config", json.dumps({"path": str(active), "effective_field": "day"}), "--activate")
    _cli(tmp_path, "acquire", "--root", "./ob", "--source", "active",
         "--stream", "returns", "--mode", "backfill")
    return tmp_path


def _weekly(sandbox, *args, script=None):
    """Run ``weekly.sh`` (or ``script``) from a different working directory, as an owner would."""
    root_of_dskit = os.path.dirname(os.path.dirname(os.path.abspath(dskit.__file__)))
    env = {**os.environ, "PYTHON": sys.executable, "PYTHONPATH": os.pathsep.join(
        part for part in (root_of_dskit, os.environ.get("PYTHONPATH", "")) if part)}
    return subprocess.run(["bash", str(script or sandbox / "weekly.sh"), *args],
                          cwd=sandbox.parent, env=env, capture_output=True, text=True)


def _decisions(sandbox):
    """``(run name, rows)`` of the one decisions file the script's decision run wrote."""
    names = sorted(os.listdir(sandbox / "decisions"))
    assert len(names) == 1, names
    with open(sandbox / "decisions" / names[0], encoding="utf-8") as fh:
        return names[0][:-len(".jsonl")], [json.loads(line) for line in fh]


def _cells(line):
    return [cell.strip() for cell in line.strip().strip("|").split(" | ")]


def test_weekly_script_runs_a_day_other_than_the_shipped_placeholder(sandbox):
    configs = {name: (sandbox / "configs" / name).read_bytes()
               for name in ("run-hold-vs-redeploy.json", "run-hold-report.json")}
    done = _weekly(sandbox, WEEK_DAY)
    assert done.returncode == 0, (done.stdout, done.stderr)

    # the decisions file: the run day's snapshot and nothing of the decoy
    run_name, rows = _decisions(sandbox)
    assert run_name.startswith(f"portfolio_policy-hold-vs-redeploy-{WEEK_DAY}-")
    assert {row["as_of"] for row in rows} == {WEEK_DAY}
    vti = next(row for row in rows if row["symbol"] == "VTI")
    # worked by hand from the shipped declarations: the aggregate row carries no acquisition
    # date, so the default class's rate taxes the gain over the basis: 2750 - 2000 = 750
    tax = _shipped(RUN_DOC, "tax")
    liq_tax = 750.0 * tax[f"{tax['default_holding']}_term_rate"]
    assert vti["market_value"] == 2750.0
    assert vti["liq_tax"] == pytest.approx(liq_tax)
    assert vti["liq_net"] == pytest.approx(2750.0 - liq_tax)

    # the page: titled with the run day, the report run named for it, the weights over the
    # run day's total 2750 + 640 = 3390 (the decoy's would be 2500 / 3100)
    page = done.stdout.strip().splitlines()[-1]
    assert re.fullmatch(rf"report/portfolio_policy-hold-report-{WEEK_DAY}-[0-9a-f]+\.md", page), page
    with open(sandbox / page, encoding="utf-8") as fh:
        text = fh.read()
    title = _shipped(REPORT_DOC, "verdict")["title"]
    assert text.startswith(f"# {title} — {WEEK_DAY}\n")
    assert SHIPPED_DAY not in text
    cells = _cells(next(line for line in text.splitlines() if f"{ACCOUNT} / VTI |" in line))
    assert cells[:2] == [f"{ACCOUNT} / VTI", f"{100 * 2750 / 3390:.1f}%"]         # weight 81.1%
    assert cells[5:7] == [f"{100 * 750 / 2750:.1f}%", f"{100 * liq_tax / 2750:.1f}%"]  # 27.3%, 6.5%
    assert cells[9] in ("EXIT", "HOLD") and cells[9] == cells[9].upper()
    assert f"- Total value: {2750.0 + 640.0:,.2f}\n" in text

    # the shipped documents are never edited: the day is substituted in temporary copies
    for name, before in configs.items():
        assert (sandbox / "configs" / name).read_bytes() == before


def test_weekly_script_names_a_failed_run_and_stops(sandbox):
    """A day with no snapshot: the decision run fails, and the owner is told which and why."""
    done = _weekly(sandbox, "2026-01-05")
    assert done.returncode != 0
    assert "weekly.sh: the pipeline run of" in done.stderr
    assert "run-hold-vs-redeploy.json" in done.stderr
    assert re.search(r"run dir: \S+portfolio_policy-hold-vs-redeploy-2026-01-05-", done.stderr), done.stderr
    assert not (sandbox / "report").exists() or os.listdir(sandbox / "report") == []


@pytest.mark.parametrize("day", ["2026-02-30", "2026-1-5", "20261027", "tomorrow", "2026-10-27 "])
def test_weekly_script_refuses_a_day_that_is_not_a_calendar_day(sandbox, day):
    done = _weekly(sandbox, day)
    assert done.returncode != 0
    assert "not a real YYYY-MM-DD day" in done.stderr
    assert not (sandbox / "decisions").exists(), "nothing was pulled or decided"


def test_weekly_script_asks_for_the_day(sandbox):
    done = _weekly(sandbox)
    assert done.returncode != 0 and "usage: bash weekly.sh" in done.stderr
