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
    _expected_rates, _jsonl, _load, _shipped, _worked, root,
)

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT_DOC = os.path.join(CHILD_ROOT, "configs", "run-hold-report.json")
WEEKLY = os.path.join(CHILD_ROOT, "weekly.sh")

#: The table's columns, in the page's order.
COLUMNS = ["Holding", "Weight", "Expected return", "Vol", "Beta", "Embedded gain", "Tax to sell",
           "Break-even", "Opportunity cost/yr", "Verdict"]


def _cells(line):
    return [cell.strip() for cell in line.strip().strip("|").split(" | ")]


def _pct(fraction):
    """A fraction as the page prints a rate, formatted here by hand: percent, one decimal."""
    return f"{100.0 * fraction:.1f}%"


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
    lines = text.splitlines()
    assert _cells(next(line for line in lines if line.startswith("| Holding"))) == COLUMNS
    vti = next(line for line in lines if " / VTI |" in line)
    # EVERY cell of VTI's line against hand-worked numbers, formatted by _pct above and not by the
    # page's own code: this is the document's column -> field mapping (a swap of two field names
    # prints two real numbers under the wrong labels). The expected rate, tax, break-even and
    # opportunity cost are worked from the shipped declarations, as test_position_summary_end_to_end
    # works them. With the placeholders shipped on 2026-10-28 the cells read: 80.6% | 3.0% | — | —
    # | 20.0% | 4.8% | 6.1% | 2.6% | EXIT.
    _, rates = _expected_rates()
    years = _shipped(RUN_DOC, "decide")["horizon_years"]
    worked = _worked(2500.0, 2000.0, rates["VTI"], decided.outputs["hurdle"]["rate"])
    hand = {"weight": 2500.0 / 3100.0, "expected_return": rates["VTI"],
            "embedded_gain": (2500.0 - 2000.0) / 2500.0, "tax_cost": worked["liq_tax"] / 2500.0,
            "break_even_return": worked["break_even"],
            "opportunity_cost": (worked["redeploy_net"] / worked["hold_net"]) ** (1.0 / years) - 1.0}
    vti_row = next(row for row in ours["records"] if row["symbol"] == "VTI")
    for field, value in hand.items():
        assert vti_row[field] == pytest.approx(value), field
    cells = _cells(vti)
    assert cells == [f"{ACCOUNT} / VTI", _pct(hand["weight"]), _pct(hand["expected_return"]),
                     "—", "—",     # no price history and no beta table reach the decided rows
                     _pct(hand["embedded_gain"]), _pct(hand["tax_cost"]),
                     _pct(hand["break_even_return"]), _pct(hand["opportunity_cost"]), "EXIT"]
    assert (cells[1], cells[5]) == ("80.6%", "20.0%"), "2500 / 3100 and 500 / 2500, by hand"
    assert len({cells[1], cells[2], cells[5], cells[6], cells[7], cells[8]}) == 6, (
        "six distinct numbers, so no two columns can swap their fields unseen")
    assert f"- Total value (listed equity longs): {ours['metrics']['total_value']:,.2f}\n" in text
    assert "- Verdicts: " in text
    assert text.rstrip().endswith("unless loss_offset is true.")
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


def _weekly(sandbox, *args, script=None, cwd=None, python=None):
    """Run ``weekly.sh`` (or ``script``) from a different working directory, as an owner would.

    ``python`` is the interpreter the script is told to use (default: this one).
    """
    root_of_dskit = os.path.dirname(os.path.dirname(os.path.abspath(dskit.__file__)))
    env = {**os.environ, "PYTHON": str(python or sys.executable), "PYTHONPATH": os.pathsep.join(
        part for part in (root_of_dskit, os.environ.get("PYTHONPATH", "")) if part)}
    return subprocess.run(["bash", str(script or sandbox / "weekly.sh"), *args],
                          cwd=cwd or sandbox.parent, env=env, capture_output=True, text=True)


def _decisions(sandbox):
    """``(run name, rows)`` of the one decisions file the script's decision run wrote."""
    names = sorted(os.listdir(sandbox / "decisions"))
    assert len(names) == 1, names
    with open(sandbox / "decisions" / names[0], encoding="utf-8") as fh:
        return names[0][:-len(".jsonl")], [json.loads(line) for line in fh]


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
    assert f"- Total value (listed equity longs): {2750.0 + 640.0:,.2f}\n" in text

    # the shipped documents are never edited: the day is substituted in temporary copies
    for name, before in configs.items():
        assert (sandbox / "configs" / name).read_bytes() == before


def test_weekly_script_names_a_failed_run_and_stops(sandbox):
    """A day with no snapshot: the decision run fails, and the owner is told which and why.

    The script exits with the pipeline's own status: 1, "error" in the exit-code table.
    """
    done = _weekly(sandbox, "2026-01-05")
    assert done.returncode == 1
    assert "exited 1;" in done.stderr
    assert "weekly.sh: the pipeline run of" in done.stderr
    assert "run-hold-vs-redeploy.json" in done.stderr
    assert re.search(r"run dir: \S+portfolio_policy-hold-vs-redeploy-2026-01-05-", done.stderr), done.stderr
    assert not (sandbox / "report").exists() or os.listdir(sandbox / "report") == []


def test_weekly_script_exits_with_a_halted_runs_status(sandbox, tmp_path):
    """A pipeline run that halts at a NO-GO gate exits 3, and the script passes the 3 on (not a 1).

    The interpreter is a stand-in that answers ``-m dskit.pipeline run`` with a halt and hands
    every other call to the real one, so no gate has to be tripped to see the status travel.
    """
    stub = tmp_path / "python-that-halts"
    stub.write_text(f'#!/usr/bin/env bash\n'
                    f'if [[ "$1 $2 $3" == "-m dskit.pipeline run" ]]; then\n'
                    f'    echo "HALTED at a gate"; exit 3\nfi\n'
                    f'exec "{sys.executable}" "$@"\n', encoding="utf-8")
    stub.chmod(0o755)
    done = _weekly(sandbox, WEEK_DAY, python=stub)
    assert done.returncode == 3, (done.returncode, done.stderr)
    assert "HALTED at a gate" in done.stderr and "exited 3" in done.stderr
    assert not (sandbox / "report").exists() or os.listdir(sandbox / "report") == []


@pytest.mark.parametrize("day", ["2026-02-30", "2026-1-5", "20261027", "tomorrow", "2026-10-27 "])
def test_weekly_script_refuses_a_day_that_is_not_a_calendar_day(sandbox, day):
    done = _weekly(sandbox, day)
    assert done.returncode != 0
    assert "not a real YYYY-MM-DD day" in done.stderr
    assert not (sandbox / "decisions").exists(), "nothing was pulled or decided"


def test_weekly_script_checks_the_day_where_it_runs_the_steps(sandbox, tmp_path):
    """The day check imports dskit from the directory the pipeline steps run in, not from the caller's.

    The caller's directory holds a decoy ``dskit`` whose date rule accepts anything; a check made
    before the ``cd`` would import it, pass 2026-02-30 and go on to pull and decide.
    """
    decoy = tmp_path / "caller"
    (decoy / "dskit" / "pipeline").mkdir(parents=True)
    (decoy / "dskit" / "__init__.py").write_text("", encoding="utf-8")
    (decoy / "dskit" / "pipeline" / "__init__.py").write_text("", encoding="utf-8")
    (decoy / "dskit" / "pipeline" / "document.py").write_text(
        "def date_problem(value):\n    return None\n", encoding="utf-8")
    done = _weekly(sandbox, "2026-02-30", cwd=decoy)
    assert done.returncode != 0
    assert "not a real YYYY-MM-DD day" in done.stderr, done.stderr
    assert not (sandbox / "decisions").exists(), "nothing was pulled or decided"


def test_weekly_script_asks_for_the_day(sandbox):
    done = _weekly(sandbox)
    assert done.returncode != 0 and "usage: bash weekly.sh" in done.stderr


# --------------------------------------------------------------------- weekly.sh, the derive step

def _derive(source, target, day, stream="", env=None):
    """Run ``weekly.sh``'s own ``derive`` function (cut out of the script) on one document."""
    script = 'py="$1"; eval "$(sed -n \'/^derive() {/,/^}/p\' "$2")"; shift 2; derive "$@"'
    done = subprocess.run(["bash", "-c", script, "derive", sys.executable, WEEKLY,
                           str(source), str(target), day, stream],
                          capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    with open(target, encoding="utf-8") as fh:
        return json.load(fh)


def _shipped_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def test_the_derived_decision_document_carries_the_run_day_in_both_places(tmp_path):
    """snapshot.where[as_of] and tax.as_of are the run day, and nothing else of the document moves."""
    assert WEEK_DAY != SHIPPED_DAY
    derived = _derive(RUN_DOC, tmp_path / "decide.json", WEEK_DAY)
    nodes = derived["pipeline"]
    assert nodes["tax"]["params"]["as_of"] == WEEK_DAY
    assert [c["value"] for c in nodes["snapshot"]["params"]["where"]
            if c["field"] == "as_of"] == [WEEK_DAY]
    # the shipped document with exactly those two values replaced is the derived one: the day
    # went nowhere else (a note naming it, a stream, a window), and no other value moved
    expected = _shipped_json(RUN_DOC)
    expected["pipeline"]["tax"]["params"]["as_of"] = WEEK_DAY
    for clause in expected["pipeline"]["snapshot"]["params"]["where"]:
        if clause["field"] == "as_of":
            clause["value"] = WEEK_DAY
    assert derived == expected
    assert SHIPPED_DAY not in json.dumps(derived["pipeline"]["tax"]["params"])


def test_the_derived_report_document_names_the_decision_run_and_keeps_the_rest(tmp_path):
    derived = _derive(REPORT_DOC, tmp_path / "report.json", WEEK_DAY, stream="some-decision-run")
    expected = _shipped_json(REPORT_DOC)
    expected["pipeline"]["decisions"]["params"]["stream"] = "some-decision-run"
    assert derived == expected, "the report has no snapshot day to set: only the stream moves"


def test_derive_reads_and_writes_utf_8_whatever_the_locale(tmp_path):
    """The shipped decision document holds an em dash; a cron job's C locale must not stop it."""
    env = {**os.environ, "LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"}
    derived = _derive(RUN_DOC, tmp_path / "decide.json", WEEK_DAY, env=env)
    assert derived["pipeline"]["hurdle"]["notes"] == (
        _shipped_json(RUN_DOC)["pipeline"]["hurdle"]["notes"])
    assert "—" in derived["pipeline"]["hurdle"]["notes"]
