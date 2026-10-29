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
from dskit.pipeline.document import date_problem, load_document
import test_hold_vs_redeploy as decision_tests
from test_hold_vs_redeploy import (  # noqa: F401  (root is a fixture)
    ACCOUNT, ACTIVE, DECIDED, LATER_DAY, OLD_VTI, RUN_DOC, THEN, TRADES, WORKED_LOTS,
    _acquire_decisions, _cli, _daily_bars, _decide, _expected_rates, _held_betas, _jsonl, _load,
    _plain_risk, _register_bars, _shipped, _worked, root,
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
    assert theirs["key_fields"] == ["account", "symbol", "lot_id"], "one row per lot"
    assert ours.pop("key_fields") == theirs.pop("key_fields") + ["as_of"]
    assert ours == theirs
    assert report.pipeline["verdict"].inputs == {"records": "$summary.records",
                                                 "portfolio": "$summary.metrics"}
    assert report.pipeline["verdict"].params["as_of_field"] == "as_of"
    assert report.pipeline["decisions"].params["key_fields"] == [
        "account", "symbol", "lot_id", "as_of"]
    assert report.hash


def test_the_report_reads_the_risk_file_of_the_same_decision_run():
    """Vol and Beta are the decision run's own numbers: its risk file, published beside its
    decisions, under the same placeholder stream (weekly.sh names both with the one run), through
    the same keyby tables the decision document's summary reads."""
    report, decide = load_document(REPORT_DOC).pipeline, load_document(RUN_DOC).pipeline
    assert report["risk_rows"].params["stream"] == report["decisions"].params["stream"]
    assert report["risk_rows"].params["root"] == report["decisions"].params["root"]
    assert report["risk_rows"].params["source"] == "risks"
    assert report["risk_rows"].params["key_fields"] == ["symbol", "as_of"]
    assert report["vols"].params == decide["vols"].params
    assert report["risk_betas"].params == decide["risk_betas"].params
    assert report["vols"].inputs == report["risk_betas"].inputs == {"records": "$risk_rows.records"}
    assert report["summary"].inputs == {"records": "$decisions.records",
                                        "betas": "$risk_betas.table", "volatilities": "$vols.table"}
    # the decision run writes the file the report reads: risk/{run}.jsonl, the stem the run name
    assert decide["risks"].params["path"] == "risk/{run}.jsonl"


def _lines_of_the_table(text):
    """The holding lines of the page's table: between the alignment row and the first blank line."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("|---")) + 1
    end = next(i for i in range(start, len(lines)) if not lines[i])
    return lines[start:end]


def _acquire_risk(home, run_name):
    """Publish the decision run's risk file back through onboarding, as weekly.sh does."""
    _cli(home, "register-source", "risks", "--root", "./ob",
         "--catalog-source", "risks-src", "--connector", "localtables",
         "--config", json.dumps({"path": str(home / "risk"), "layout": "file",
                                 "effective_field": "as_of"}), "--activate")
    _cli(home, "acquire", "--root", "./ob", "--source", "risks",
         "--stream", run_name, "--mode", "backfill")


def test_hold_report_end_to_end(root, monkeypatch):  # noqa: F811  (the imported fixture)
    monkeypatch.chdir(root)
    (root / "report").mkdir()
    decided = _decide(root)
    run_name = os.path.basename(decided.run_dir)
    _acquire_decisions(root, run_name)
    _acquire_risk(root, run_name)
    document = _load(REPORT_DOC, str(root), decisions={"stream": run_name},
                     risk_rows={"stream": run_name})
    # a year later: the title is the decisions' own day, never the day the report is run
    result = run_document(document, asof=LATER_DAY, journal=False)
    assert result.state == "ran", (result.state, result.error)
    # the recomputed summary is the decision run's, number for number, betas and volatilities
    # included (read back from the published risk file); as_of rides along as a key field
    ours = result.outputs["summary"]
    theirs = decided.outputs["summary"]
    assert {row["as_of"] for row in ours["records"]} == {DECIDED}
    assert theirs["records"] == [{k: v for k, v in row.items() if k != "as_of"}
                                 for row in ours["records"]]
    assert all(row["volatility"] is not None and row["beta"] is not None
               for row in ours["records"])
    assert theirs["metrics"] == ours["metrics"]
    written = result.outputs["verdict"]["path"]
    assert os.path.dirname(os.path.abspath(written)) == str(root / "report")
    assert os.path.basename(written) == os.path.basename(result.run_dir) + ".md"
    with open(written, encoding="utf-8") as fh:
        text = fh.read()
    title = document.pipeline["verdict"].params["title"]
    assert text.startswith(f"# {title} — {DECIDED}\n") and LATER_DAY not in text
    # one line per LOT, highest opportunity cost first, each named symbol / lot id
    table = _lines_of_the_table(text)
    rows = sorted(ours["records"], key=lambda r: -r["opportunity_cost"])
    assert len(rows) == len(WORKED_LOTS) == 4 and len(table) == 4
    assert [line.split(" | ")[0][2:] for line in table] == [
        f"{r['symbol']} / {r['lot_id']}" for r in rows]
    assert {r["lot_id"] for r in rows} == set(WORKED_LOTS)
    lines = text.splitlines()
    assert _cells(next(line for line in lines if line.startswith("| Holding"))) == COLUMNS
    old = next(line for line in table if OLD_VTI in line)
    # EVERY cell of the old VTI lot's line against hand-worked numbers, formatted by _pct above
    # and not by the page's own code: this is the document's column -> field mapping (a swap of two
    # field names prints two real numbers under the wrong labels). Vol and beta are the plain-Python
    # recomputation from the written bars (no dskit), which is what the decision run priced.
    # The expected rate, tax, break-even and opportunity cost are worked from the shipped
    # declarations, as test_position_summary_end_to_end works them.
    risk = _plain_risk(root, DECIDED)["VTI"]
    _, rates = _expected_rates(_held_betas(root))
    years = _shipped(RUN_DOC, "decide")["horizon_years"]
    worked = _worked(1500.0, 1080.0, "long", rates["VTI"], decided.outputs["hurdle"]["rate"])
    hand = {"weight": 1500.0 / 4350.0, "expected_return": rates["VTI"],
            "volatility": risk["annual_vol"], "beta": risk["beta"],
            "embedded_gain": (1500.0 - 1080.0) / 1500.0, "tax_cost": worked["liq_tax"] / 1500.0,
            "break_even_return": worked["break_even"],
            "opportunity_cost": (worked["redeploy_net"] / worked["hold_net"]) ** (1.0 / years) - 1.0}
    old_row = next(row for row in ours["records"] if row["lot_id"] == OLD_VTI)
    for field, value in hand.items():
        assert old_row[field] == pytest.approx(value, rel=1e-9), field
    cells = _cells(old)
    assert cells == [f"VTI / {OLD_VTI}", _pct(hand["weight"]), _pct(hand["expected_return"]),
                     _pct(hand["volatility"]), f"{hand['beta']:.2f}",
                     _pct(hand["embedded_gain"]), _pct(hand["tax_cost"]),
                     _pct(hand["break_even_return"]), _pct(hand["opportunity_cost"]),
                     "EXIT" if old_row["decision"] == "exit" else "HOLD"]
    assert (cells[1], cells[5]) == ("34.5%", "28.0%"), "1500 / 4350 and 420 / 1500, by hand"
    assert len({cells[1], cells[2], cells[3], cells[5], cells[6], cells[7], cells[8]}) == 7, (
        "seven distinct percentages, so no two columns can swap their fields unseen")
    assert float(cells[4]) == pytest.approx(0.67 * 1.0 + 0.33, abs=0.1), "the VTI factor is 1.0"
    # the other symbol's lot carries ITS risk, not VTI's: the table is joined by symbol
    aapl = _cells(next(line for line in table if "AAPL" in line))
    aapl_risk = _plain_risk(root, DECIDED)["AAPL"]
    assert (aapl[3], aapl[4]) == (_pct(aapl_risk["annual_vol"]), f"{aapl_risk['beta']:.2f}")
    assert aapl[3] != cells[3] and aapl[4] != cells[4]
    assert f"- Total value (stock, ETF and mutual fund longs): {ours['metrics']['total_value']:,.2f}\n" in text
    assert "- Verdicts: " in text
    assert text.rstrip().endswith("unless loss_offset is true.")
    assert result.outputs["verdict"]["metrics"]["rows"] == len(rows)


def test_a_lot_cell_is_not_cut_for_a_real_accounts_hash():
    """Schwab's account hash is 64 characters. The Holding cell joins the verdict's key fields, and
    the lot id holds the account once: a cell past the renderer's limit is cut, which would hide the
    day and the ordinal that tell two lots of one holding apart."""
    from dskit.pipeline.runs import render_cell

    account = "A1B2C3D4" * 8
    row = {"account": account, "symbol": "GOOGL", "lot_id": f"{account}:GOOGL:2024-03-01:12"}
    keys = load_document(REPORT_DOC).pipeline["verdict"].params["key_fields"]
    cell = " / ".join(str(row[k]) for k in keys)
    assert render_cell(cell) == cell and cell.endswith(":2024-03-01:12")
    # all three fields would not fit: why the account is not a key field of the verdict
    assert len(" / ".join(str(row[k]) for k in ("account", "symbol", "lot_id"))) > 120


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
#: The second VTI lot is bought 363 days before the run day: SHORT term on it (the shipped
#: threshold is 366 days), LONG on the placeholder day seven days later. A third VTI buy falls
#: between the two days: replayed only if the lots' day is the placeholder, which the snapshot
#: (ten shares on both days) then refuses.
YOUNG_DAY = (date.fromisoformat(WEEK_DAY) - timedelta(days=363)).isoformat()
AFTER_DAY = (date.fromisoformat(WEEK_DAY) + timedelta(days=3)).isoformat()
OLD_LOT, AAPL_LOT = f"{ACCOUNT}:VTI:2023-06-15:1", f"{ACCOUNT}:AAPL:2024-03-01:1"
YOUNG_LOT = f"{ACCOUNT}:VTI:{YOUNG_DAY}:1"
#: Each lot on the run day: (symbol, value, basis, holding class). VTI's ten shares are worth 2750
#: (6 and 4 shares: 1650 and 1100); the decoy day's 2500 would split 1500 and 1000.
RUN_LOTS = {OLD_LOT: ("VTI", 1650.0, 1080.0, "long"), YOUNG_LOT: ("VTI", 1100.0, 920.0, "short"),
            AAPL_LOT: ("AAPL", 640.0, 450.0, "long")}


def _before(day):
    """The last weekday strictly before ``day``: the latest session a pull on ``day`` can hold."""
    found = date.fromisoformat(day) - timedelta(days=1)
    while found.weekday() >= 5:
        found -= timedelta(days=1)
    return found.isoformat()


def _snapshot(day, vti, aapl):
    """THEN's four rows (two equity longs, a short, an option) re-dated and re-valued."""
    return [{**THEN[0], "as_of": day, "market_value": vti},
            {**THEN[1], "as_of": day, "market_value": aapl},
            {**THEN[2], "as_of": day}, {**THEN[3], "as_of": day}]


def _trades():
    """The purchases behind the snapshot's holdings, and one after the run day."""
    young = {**TRADES[1], "trade_date": YOUNG_DAY, "time": f"{YOUNG_DAY}T14:02:00+0000"}
    after = {**TRADES[3], "transaction_id": 1009, "trade_date": AFTER_DAY,
             "time": f"{AFTER_DAY}T14:10:00+0000"}
    return [TRADES[0], young, TRADES[2], after]


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """The script and the shipped configs beside a fresh onboarding root, set up as the README does.

    ``schwab`` is a registered ``localfiles`` source holding both snapshots, NOT yet acquired
    (the script's own pull does that). The README's step 3 pulls are done: the trades, the daily
    bars (sessions up to the day before the PLACEHOLDER day, so a later session than the run day
    exists and must not be read) and the active series (52 weekly returns meet the shipped
    document's floor of one year).
    """
    assert WEEK_DAY != SHIPPED_DAY
    assert len(ACTIVE) == 12
    monkeypatch.setattr(decision_tests, "LAST_SESSION", _before(SHIPPED_DAY))
    shutil.copy(WEEKLY, tmp_path / "weekly.sh")
    shutil.copytree(os.path.join(CHILD_ROOT, "configs"), tmp_path / "configs")
    data, active, history = tmp_path / "data", tmp_path / "active", tmp_path / "history"
    for folder in (data, active, history):
        folder.mkdir()
    _jsonl(data / "positions.jsonl",
           [row for day, (vti, aapl) in VALUES.items() for row in _snapshot(day, vti, aapl)])
    _jsonl(history / "trades.jsonl", _trades())
    start = date(2025, 1, 6)
    _jsonl(active / "returns.jsonl", [
        {"period": f"week-{i:02d}", "net_return": value, "day": (start + timedelta(days=7 * i)).isoformat()}
        for i, value in enumerate((ACTIVE * 5)[:52])])
    _cli(tmp_path, "init", "--root", "./ob")
    _cli(tmp_path, "register-source", "schwab", "--root", "./ob",
         "--catalog-source", "schwab-src", "--connector", "localfiles",
         "--config", json.dumps({"path": str(data), "effective_field": "as_of"}), "--activate")
    _cli(tmp_path, "register-source", "schwab_trades", "--root", "./ob",
         "--catalog-source", "schwab-trades-src", "--connector", "localfiles",
         "--config", json.dumps({"path": str(history), "effective_field": "trade_date"}),
         "--activate")
    _cli(tmp_path, "acquire", "--root", "./ob", "--source", "schwab_trades",
         "--stream", "trades", "--mode", "backfill")
    _register_bars(tmp_path, "schwab_daily", "daily", _daily_bars())
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


def _one_file(folder):
    """``(run name, rows)`` of the one ``.jsonl`` file a run wrote into ``folder``."""
    names = sorted(os.listdir(folder))
    assert len(names) == 1, names
    with open(folder / names[0], encoding="utf-8") as fh:
        return names[0][:-len(".jsonl")], [json.loads(line) for line in fh]


def _decisions(sandbox):
    """``(run name, rows)`` of the one decisions file the script's decision run wrote."""
    return _one_file(sandbox / "decisions")


def test_weekly_script_runs_a_day_other_than_the_shipped_placeholder(sandbox):
    configs = {name: (sandbox / "configs" / name).read_bytes()
               for name in ("run-hold-vs-redeploy.json", "run-hold-report.json")}
    done = _weekly(sandbox, WEEK_DAY)
    assert done.returncode == 0, (done.stdout, done.stderr)

    # the decisions file: the run day's snapshot and nothing of the decoy, one row per lot
    run_name, rows = _decisions(sandbox)
    assert run_name.startswith(f"portfolio_policy-hold-vs-redeploy-{WEEK_DAY}-")
    assert {row["as_of"] for row in rows} == {WEEK_DAY}
    by_lot = {row["lot_id"]: row for row in rows}
    assert set(by_lot) == set(RUN_LOTS), "one row per lot; the buy after the run day is not replayed"
    tax = _shipped(RUN_DOC, "tax")
    for lot_id, (symbol, value, basis, holding) in RUN_LOTS.items():
        row = by_lot[lot_id]
        # worked by hand from the shipped declarations: each lot is valued at the run day's price
        # (the decoy day's would be 1500 / 1000 / 600) and taxed by its own holding period AT THE
        # RUN DAY: the second lot is 363 days old, short term; on the placeholder day it is long
        liq_tax = (value - basis) * tax[f"{holding}_term_rate"]
        assert (row["symbol"], row["market_value"], row["liq_holding"]) == (symbol, value, holding)
        assert row["liq_tax"] == pytest.approx(liq_tax)
        assert row["liq_net"] == pytest.approx(value - liq_tax)

    # the risk file: read at the last session on or before the run day, though the bars run on to
    # the day before the placeholder day (so the risk day moved too, not only the reference date)
    risk_name, risk_rows = _one_file(sandbox / "risk")
    assert risk_name == run_name
    risk = _plain_risk(sandbox, WEEK_DAY)
    assert {row["symbol"] for row in risk_rows} == {"AAPL", "SPY", "VTI"}
    last_bar = _before(SHIPPED_DAY)
    assert {row["as_of"] for row in risk_rows} == {risk[s]["as_of"] for s in risk} != {last_bar}
    assert risk["VTI"]["as_of"] <= WEEK_DAY < last_bar, "bars exist after the run day"
    for row in risk_rows:
        assert row["annual_vol"] == pytest.approx(risk[row["symbol"]]["annual_vol"], rel=1e-9)
        assert row["beta"] == pytest.approx(risk[row["symbol"]]["beta"], rel=1e-9)

    # the page: titled with the run day, the report run named for it, one line per lot, the weights
    # over the run day's total 1650 + 1100 + 640 = 3390 (the decoy's would be 2500 + 600), and
    # each lot's own volatility and beta, never an em dash
    page = done.stdout.strip().splitlines()[-1]
    assert re.fullmatch(rf"report/portfolio_policy-hold-report-{WEEK_DAY}-[0-9a-f]+\.md", page), page
    with open(sandbox / page, encoding="utf-8") as fh:
        text = fh.read()
    title = _shipped(REPORT_DOC, "verdict")["title"]
    assert text.startswith(f"# {title} — {WEEK_DAY}\n")
    assert SHIPPED_DAY not in text
    table = _lines_of_the_table(text)
    assert len(table) == len(RUN_LOTS)
    cells = {lot_id: _cells(next(line for line in table if lot_id in line)) for lot_id in RUN_LOTS}
    for lot_id, (symbol, value, basis, holding) in RUN_LOTS.items():
        liq_tax = (value - basis) * tax[f"{holding}_term_rate"]
        line = cells[lot_id]
        assert line[0] == f"{symbol} / {lot_id}"
        assert line[1] == _pct(value / 3390.0)
        assert line[3:5] == [_pct(risk[symbol]["annual_vol"]), f"{risk[symbol]['beta']:.2f}"]
        assert line[5:7] == [_pct((value - basis) / value), _pct(liq_tax / value)]
        assert line[9] in ("EXIT", "HOLD")
    assert cells[OLD_LOT][1] == "48.7%" and cells[OLD_LOT][5] == "34.5%", "1650 / 3390; 570 / 1650"
    assert f"- Total value (stock, ETF and mutual fund longs): {3390.0:,.2f}\n" in text

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


def _day_places(node):
    """Every place a string equal to a day is written in a document, outside ``notes`` prose, as
    ``(path, value)`` pairs: where the decision day is declared, found by value, not by name."""
    found = []

    def walk(value, path):
        if isinstance(value, dict):
            for key, item in value.items():
                if key != "notes":
                    walk(item, path + (key,))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, path + (index,))
        elif isinstance(value, str) and not date_problem(value):
            found.append((path, value))

    walk(node, ())
    return found


def _set_pinned_days(pipeline, day):
    """The decision document's five declarations of the decision day, replaced: the ones
    ``test_decision_day_is_declared_once_per_document`` pins equal."""
    pipeline["snapshot"]["params"]["where"][0]["value"] = day
    pipeline["lots"]["params"]["as_of"] = day
    pipeline["tax"]["params"]["as_of"] = day
    pipeline["risk_day"]["params"]["where"][0]["value"] = day
    pipeline["risk_latest"]["params"]["reference_date"] = day


def test_the_derived_decision_document_carries_the_run_day_in_every_pinned_place(tmp_path):
    """The five pinned days (snapshot, lots, tax, risk day, risk reference) are the run day, and
    nothing else of the document moves. The shipped document is searched by value for days: exactly
    those five exist, so a sixth declaration (which the pin test would then have to cover) fails
    here rather than being left at the placeholder in the derived copy."""
    assert WEEK_DAY != SHIPPED_DAY
    shipped = _shipped_json(RUN_DOC)
    pinned = {tuple(path) for path, _ in _day_places(shipped["pipeline"])}
    assert pinned == {("snapshot", "params", "where", 0, "value"), ("lots", "params", "as_of"),
                      ("tax", "params", "as_of"), ("risk_day", "params", "where", 0, "value"),
                      ("risk_latest", "params", "reference_date")}
    assert {value for _, value in _day_places(shipped["pipeline"])} == {SHIPPED_DAY}
    derived = _derive(RUN_DOC, tmp_path / "decide.json", WEEK_DAY)
    assert {value for _, value in _day_places(derived["pipeline"])} == {WEEK_DAY}
    assert {tuple(path) for path, _ in _day_places(derived["pipeline"])} == pinned
    # each place by name, so the helper above is not the only witness
    nodes = derived["pipeline"]
    assert nodes["tax"]["params"]["as_of"] == nodes["lots"]["params"]["as_of"] == WEEK_DAY
    assert nodes["risk_latest"]["params"]["reference_date"] == WEEK_DAY
    assert [c["value"] for c in nodes["snapshot"]["params"]["where"] if c["field"] == "as_of"] == [WEEK_DAY]
    assert nodes["risk_day"]["params"]["where"] == [{"field": "ts", "op": "<=", "value": WEEK_DAY}]
    # the shipped document with exactly those five values replaced is the derived one: the day went
    # nowhere else (a note naming it, a stream, a window), and no other value moved
    _set_pinned_days(shipped["pipeline"], WEEK_DAY)
    assert derived == shipped
    assert SHIPPED_DAY not in json.dumps(derived["pipeline"]["tax"]["params"])


def test_the_derived_documents_satisfy_the_configs_pins(tmp_path):
    """The derived decision document passes the pin test_configs makes of the shipped one: one
    decision day, in all five places. (The pin reads the shipped file; this reads the copy.)"""
    derived = _derive(RUN_DOC, tmp_path / "decide.json", WEEK_DAY)
    pipeline = load_document(str(tmp_path / "decide.json")).pipeline
    where = {c["field"]: c["value"] for c in pipeline["snapshot"].params["where"]}
    (risk_day,) = pipeline["risk_day"].params["where"]
    assert where["as_of"] == pipeline["tax"].params["as_of"] == pipeline["lots"].params["as_of"] == WEEK_DAY
    assert risk_day == {"field": "ts", "op": "<=", "value": WEEK_DAY}
    assert pipeline["risk_latest"].params["reference_date"] == WEEK_DAY
    assert derived["pipeline"]["summary"]["params"]["key_fields"] == ["account", "symbol", "lot_id"]


def test_the_derived_report_document_names_the_decision_run_in_both_streams(tmp_path):
    derived = _derive(REPORT_DOC, tmp_path / "report.json", WEEK_DAY, stream="some-decision-run")
    expected = _shipped_json(REPORT_DOC)
    placeholder = expected["pipeline"]["decisions"]["params"]["stream"]
    assert expected["pipeline"]["risk_rows"]["params"]["stream"] == placeholder
    for node in ("decisions", "risk_rows"):
        expected["pipeline"][node]["params"]["stream"] = "some-decision-run"
    assert derived == expected, "the report has no day to set: only its two streams move"
    assert placeholder not in json.dumps(derived)


def test_derive_reads_and_writes_utf_8_whatever_the_locale(tmp_path):
    """The shipped decision document holds an em dash; a cron job's C locale must not stop it."""
    env = {**os.environ, "LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"}
    derived = _derive(RUN_DOC, tmp_path / "decide.json", WEEK_DAY, env=env)
    assert derived["pipeline"]["hurdle"]["notes"] == (
        _shipped_json(RUN_DOC)["pipeline"]["hurdle"]["notes"])
    assert "—" in derived["pipeline"]["hurdle"]["notes"]
