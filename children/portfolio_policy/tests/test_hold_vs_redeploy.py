"""Both documents end to end, in their SHIPPED shape, on a synthetic onboarding root.

One ``positions`` stream holds two snapshots a year apart; the decision run
filters to the earlier day and the scoring run to the later one, exactly as
the documents are written. The onboarding root is built the way the owner will
build it — the CLI verbs, from the child root — with ``localfiles`` standing in
for ``schwab_positions`` (the observations kind reads the root, never the
connector). The numbers are worked by hand from the documents' own rates.
"""

import json
import os
import subprocess
import sys
from dataclasses import replace

import pytest

from dskit.pipeline import OutputsConfig, run_document
from dskit.pipeline.document import load_document

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_DOC = os.path.join(CHILD_ROOT, "configs", "run-hold-vs-redeploy.json")
OUTCOMES_DOC = os.path.join(CHILD_ROOT, "configs", "run-decision-outcomes.json")

DECIDED, LATER_DAY = "2025-10-27", "2026-10-27"
ACCOUNT = "F00DBEEFCAFE4C0DE"

THEN = [
    {"account": ACCOUNT, "symbol": "VTI", "asset_type": "EQUITY", "as_of": DECIDED,
     "long_quantity": 10.0, "short_quantity": 0.0, "average_price": 200.0,
     "cost_basis": 2000.0, "market_value": 2500.0},
    {"account": ACCOUNT, "symbol": "AAPL", "asset_type": "EQUITY", "as_of": DECIDED,
     "long_quantity": 3.0, "short_quantity": 0.0, "average_price": 150.0,
     "cost_basis": 450.0, "market_value": 600.0},
    {"account": ACCOUNT, "symbol": "SPY   261218C00500000", "asset_type": "OPTION",
     "as_of": DECIDED, "long_quantity": 1.0, "short_quantity": 0.0, "average_price": 12.0,
     "cost_basis": 12.0, "market_value": 1500.0},
]
# a year later (today, so the snapshot is not a forecast): VTI doubled its share count and
# its price rose 10%; AAPL fell 10%; the option is gone
LATER = [
    {**THEN[0], "as_of": LATER_DAY, "long_quantity": 20.0, "market_value": 5500.0,
     "cost_basis": 4500.0},
    {**THEN[1], "as_of": LATER_DAY, "market_value": 540.0},
]

# Twelve weekly net returns with mean 0.0055 and sd 0.0087; among five trials the
# expected max null Sharpe is 0.3978 (pinned in dskit's test_active_hurdle).
ACTIVE = [0.01, -0.005, 0.02, 0.0, 0.015, -0.01, 0.005, 0.01, 0.012, -0.002, 0.008, 0.003]
EMAX_5 = 0.39777384449675074


def _cli(cwd, *args):
    done = subprocess.run([sys.executable, "-m", "dskit.onboarding", *args], cwd=cwd,
                          capture_output=True, text=True)
    assert done.returncode == 0, (args, done.stdout, done.stderr)


def _jsonl(path, rows):
    with open(path, "w") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


@pytest.fixture
def root(tmp_path):
    """An onboarding root under ``tmp_path/ob``: two positions snapshots, the active series."""
    data, active = tmp_path / "data", tmp_path / "active"
    data.mkdir()
    active.mkdir()
    _jsonl(data / "positions.jsonl", THEN + LATER)
    _jsonl(active / "returns.jsonl", [
        {"period": f"2025-W{i + 1:02d}", "net_return": v,
         "day": f"2025-{(i // 4) + 1:02d}-{(i % 4) * 7 + 1:02d}"}
        for i, v in enumerate(ACTIVE)])
    for name in ("decisions", "outcomes"):
        (tmp_path / name).mkdir()
    _cli(tmp_path, "init", "--root", "./ob")
    _cli(tmp_path, "register-source", "schwab", "--root", "./ob",
         "--catalog-source", "schwab-src", "--connector", "localfiles",
         "--config", json.dumps({"path": str(data), "effective_field": "as_of"}), "--activate")
    _cli(tmp_path, "acquire", "--root", "./ob", "--source", "schwab",
         "--stream", "positions", "--mode", "backfill")
    _cli(tmp_path, "register-source", "active", "--root", "./ob",
         "--catalog-source", "active-src", "--connector", "localfiles",
         "--config", json.dumps({"path": str(active), "effective_field": "day"}), "--activate")
    _cli(tmp_path, "acquire", "--root", "./ob", "--source", "active",
         "--stream", "returns", "--mode", "backfill")
    return tmp_path


def _load(path, run_root, **overrides):
    """Load a document, override run_root, and set the dated/named values a run needs."""
    with open(path) as fh:
        raw = json.load(fh)
    for node, params in overrides.items():
        raw["pipeline"][node]["params"].update(params)
    out = os.path.join(run_root, os.path.basename(path))
    with open(out, "w") as fh:
        json.dump(raw, fh)
    document = load_document(out)
    return replace(document, outputs=OutputsConfig(run_root=os.path.join(run_root, "runs")))


def _decide(root):
    document = _load(RUN_DOC, str(root),
                     snapshot={"where": [{"field": "as_of", "op": "==", "value": DECIDED},
                                         {"field": "asset_type", "op": "==", "value": "EQUITY"}]},
                     tax={"as_of": DECIDED})
    result = run_document(document, asof=DECIDED, journal=False)
    assert result.state == "ran", (result.state, result.error)
    return result


def test_documents_are_valid_and_wire_their_stages():
    decide = load_document(RUN_DOC)
    assert list(decide.pipeline) == [
        "positions", "snapshot", "tax", "market", "betas", "active_returns", "hurdle",
        "decide", "decisions"]
    where = decide.pipeline["snapshot"].params["where"]
    assert [clause["field"] for clause in where] == ["as_of", "asset_type"]
    assert decide.pipeline["tax"].params["as_of"] == where[0]["value"]
    outcomes = load_document(OUTCOMES_DOC)
    assert list(outcomes.pipeline) == ["decisions", "positions", "later", "outcomes", "scored"]
    assert outcomes.pipeline["outcomes"].params["key_fields"] == ["account", "symbol"]
    assert decide.hash and outcomes.hash


def test_hold_vs_redeploy_end_to_end(root, monkeypatch):
    monkeypatch.chdir(root)
    result = _decide(root)
    rows = {row["symbol"]: row for row in result.outputs["decide"]["records"]}
    assert set(rows) == {"VTI", "AAPL"}, "one snapshot day, equities only"
    vti = rows["VTI"]
    # default_holding long at 0.238, measured to --asof: tax 500 x .238 = 119, net 2381
    assert result.outputs["tax"]["summary"]["as_of"] == DECIDED
    assert vti["liq_holding"] == "long"
    assert vti["liq_tax"] == pytest.approx(119.0)
    assert vti["liq_net"] == pytest.approx(2381.0)
    # market: .013 + .02 + .025 + ((27/36)^(1/10) - 1) = 0.058 - 0.028366 = 0.029634
    market = 0.058 + (27.0 / 36.0) ** 0.1 - 1.0
    assert result.outputs["market"]["rate"] == pytest.approx(market)
    rates = result.outputs["betas"]["rates"]
    assert rates["VTI"] == pytest.approx(0.04 + 1.0 * (market - 0.04))
    assert rates["AAPL"] == pytest.approx(0.04 + 1.2 * (market - 0.04))
    # hurdle: (0.0055 - 0.3978 x 0.0087) x 52 = 0.1060, admissible (dsr 0.76 >= 0.5)
    mean = sum(ACTIVE) / len(ACTIVE)
    sd = (sum((x - mean) ** 2 for x in ACTIVE) / (len(ACTIVE) - 1)) ** 0.5
    hurdle = (mean - EMAX_5 * sd) * 52
    assert result.outputs["hurdle"]["rate"] == pytest.approx(hurdle)
    assert result.outputs["hurdle"]["admissible"] is True
    # hold 2500 x (1+VTI)^5, gain over 2000 taxed at .238 once;
    # redeploy 2381 x (1 + (hurdle - .02) x (1 - .408))^5, taxed every year, nothing at the end
    hold_value = 2500.0 * (1.0 + rates["VTI"]) ** 5
    redeploy_value = 2381.0 * (1.0 + (hurdle - 0.02) * (1.0 - 0.408)) ** 5
    assert vti["hold_value"] == pytest.approx(hold_value)
    assert vti["hold_net"] == pytest.approx(hold_value - max(hold_value - 2000.0, 0.0) * 0.238)
    assert vti["redeploy_value"] == pytest.approx(redeploy_value)
    assert vti["redeploy_terminal_tax"] == 0.0
    assert vti["redeploy_net"] == pytest.approx(redeploy_value)
    # by hand: hold_net 2680.58 vs redeploy_net 3052.07 -> exit
    assert vti["hold_net"] == pytest.approx(2680.58, abs=0.01)
    assert vti["redeploy_net"] == pytest.approx(3052.07, abs=0.01)
    assert vti["decision"] == "exit"
    summary = result.outputs["decide"]["summary"]
    assert (summary["hold_return_source"], summary["redeploy_return_source"]) == ("wired", "wired")
    assert summary["redeploy_tax_timing"] == "annual"
    written = result.outputs["decisions"]["path"]
    assert os.path.dirname(os.path.abspath(written)) == str(root / "decisions")
    with open(written) as fh:
        lines = fh.read().splitlines()
    assert len(lines) == 2
    assert all(json.loads(line)["run_id"] == os.path.basename(result.run_dir) for line in lines)


def test_outcomes_score_the_decision_run_a_year_later(root, monkeypatch):
    monkeypatch.chdir(root)
    decided = _decide(root)
    run_name = os.path.basename(decided.run_dir)
    _cli(root, "register-source", "decisions", "--root", "./ob",
         "--catalog-source", "decisions-src", "--connector", "localtables",
         "--config", json.dumps({"path": str(root / "decisions"), "layout": "file",
                                 "effective_field": "as_of"}),
         "--activate")
    _cli(root, "acquire", "--root", "./ob", "--source", "decisions",
         "--stream", run_name, "--mode", "backfill")
    document = _load(OUTCOMES_DOC, str(root),
                     decisions={"stream": run_name},
                     later={"where": [{"field": "as_of", "op": "==", "value": LATER_DAY}]})
    result = run_document(document, asof=LATER_DAY, journal=False)
    assert result.state == "ran", (result.state, result.error)
    rows = {row["symbol"]: row for row in result.outputs["outcomes"]["records"]}
    assert set(rows) == {"VTI", "AAPL"}
    assert rows["VTI"]["account"] == ACCOUNT
    assert rows["VTI"]["realized_growth"] == pytest.approx(1.1)
    assert rows["AAPL"]["realized_growth"] == pytest.approx(0.9)
    decided_vti = {r["symbol"]: r for r in decided.outputs["decide"]["records"]}["VTI"]
    elapsed = 365 / 365.25
    assert rows["VTI"]["projected_growth"] == pytest.approx(
        (decided_vti["hold_value"] / 2500.0) ** (elapsed / 5))
    assert result.outputs["outcomes"]["summary"]["unmatched"] == []
    assert os.path.isfile(result.outputs["scored"]["path"])
