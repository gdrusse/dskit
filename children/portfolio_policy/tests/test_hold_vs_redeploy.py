"""Both documents end to end, in their SHIPPED shape, on a synthetic onboarding root.

One ``positions`` stream holds two snapshots a year apart; the decision run
filters to the earlier day and the scoring run to the later one, exactly as
the documents are written. The onboarding root is built the way the owner will
build it — the CLI verbs, from the child root — with ``localfiles`` standing in
for ``schwab_positions`` (the observations kind reads the root, never the
connector). The expectations are worked by hand in this file from the rates the
loaded documents declare, never from literals, so an owner replacing a
placeholder keeps the suite green. With the placeholders shipped on 2026-10-27
the same arithmetic gives, for VTI: tax 119, net 2381, hold_net 2680.58 against
redeploy_net 3052.07, so ``exit``.
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
    {"account": ACCOUNT, "symbol": "XYZ", "asset_type": "EQUITY", "as_of": DECIDED,
     "long_quantity": 0.0, "short_quantity": 4.0, "average_price": 50.0,
     "cost_basis": -200.0, "market_value": -180.0},
    {"account": ACCOUNT, "symbol": "SPY   261218C00500000", "asset_type": "OPTION",
     "as_of": DECIDED, "long_quantity": 1.0, "short_quantity": 0.0, "average_price": 12.0,
     "cost_basis": 12.0, "market_value": 1500.0},
]
# a year later (today, so the snapshot is not a forecast): VTI doubled its share count and its
# price rose 10%; AAPL fell 10%; the short XYZ (zero long quantity) and the option are still
# there, undecided, exactly the rows a real later pull carries
LATER = [
    {**THEN[0], "as_of": LATER_DAY, "long_quantity": 20.0, "market_value": 5500.0,
     "cost_basis": 4500.0},
    {**THEN[1], "as_of": LATER_DAY, "market_value": 540.0},
    {**THEN[2], "as_of": LATER_DAY},
    {**THEN[3], "as_of": LATER_DAY},
]

# Twelve weekly net returns with mean 0.0055 and sd 0.0087; among FIVE trials the expected max
# null Sharpe is 0.3978 (pinned in dskit's test_active_hurdle). The e2e run therefore pins the
# hurdle's trials and min_dsr to the values that figure belongs to (as it already pins
# min_periods to the twelve returns it has); every other number is read from the document.
ACTIVE = [0.01, -0.005, 0.02, 0.0, 0.015, -0.01, 0.005, 0.01, 0.012, -0.002, 0.008, 0.003]
EMAX_5 = 0.39777384449675074
HURDLE_PINS = {"min_periods": 12, "trials": 5, "min_dsr": 0.5}


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


def _shipped(path, node):
    """The params one node of a shipped document declares: where every expectation reads from."""
    return load_document(path).pipeline[node].params


def _shipped_where(path, node, day):
    """A shipped filter with only its date replaced, so every other clause (equities only,
    longs only) runs exactly as shipped."""
    where = _shipped(path, node)["where"]
    return [{**clause, "value": day} if clause["field"] == "as_of" else clause for clause in where]


def _expected_rates():
    """``(market, {symbol: rate})`` worked by hand from the decision document's declarations:
    market = yield + growth + inflation + ((fair CAPE / CAPE) ** (1 / years) - 1);
    symbol = risk_free + beta x (market - risk_free)."""
    m, b = _shipped(RUN_DOC, "market"), _shipped(RUN_DOC, "betas")
    market = (m["dividend_yield"] + m["real_growth"] + m["inflation"]
              + (m["fair_cape"] / m["cape"]) ** (1.0 / m["reversion_years"]) - 1.0)
    return market, {symbol: b["risk_free"] + beta * (market - b["risk_free"])
                    for symbol, beta in b["betas"].items()}


def _decide(root):
    # twelve weekly returns stand in for a year: the shipped floor is 52 (one year), and the
    # E[max null Sharpe] worked below is for five trials of this series
    document = _load(RUN_DOC, str(root),
                     snapshot={"where": _shipped_where(RUN_DOC, "snapshot", DECIDED)},
                     tax={"as_of": DECIDED}, hurdle=HURDLE_PINS)
    result = run_document(document, asof=DECIDED, journal=False)
    assert result.state == "ran", (result.state, result.error)
    return result


def test_documents_are_valid_and_wire_their_stages():
    decide = load_document(RUN_DOC)
    assert list(decide.pipeline) == [
        "positions", "snapshot", "tax", "market", "betas", "active_returns", "hurdle",
        "decide", "decisions"]
    where = decide.pipeline["snapshot"].params["where"]
    assert [clause["field"] for clause in where] == ["as_of", "asset_type", "long_quantity"]
    assert decide.pipeline["tax"].params["as_of"] == where[0]["value"]
    assert where[2] == {"field": "long_quantity", "op": ">", "value": 0}, "shorts are filtered out"
    assert decide.pipeline["hurdle"].params["min_periods"] == 52, "one year of weekly returns"
    outcomes = load_document(OUTCOMES_DOC)
    later = outcomes.pipeline["later"].params["where"]
    assert [clause["field"] for clause in later] == ["as_of", "asset_type", "long_quantity"]
    assert later[2] == where[2], "the scoring snapshot drops shorts too"
    assert list(outcomes.pipeline) == ["decisions", "positions", "later", "outcomes", "scored"]
    assert outcomes.pipeline["outcomes"].params["key_fields"] == ["account", "symbol"]
    assert decide.hash and outcomes.hash


def test_hold_vs_redeploy_end_to_end(root, monkeypatch):
    monkeypatch.chdir(root)
    result = _decide(root)
    tax, decide = _shipped(RUN_DOC, "tax"), _shipped(RUN_DOC, "decide")
    hurdle_periods = _shipped(RUN_DOC, "hurdle")["periods_per_year"]
    rows = {row["symbol"]: row for row in result.outputs["decide"]["records"]}
    assert set(rows) == {"VTI", "AAPL"}, "one snapshot day, equities only, no short XYZ"
    vti = rows["VTI"]
    # aggregate rows carry no date, so the declared default class and its rate apply, measured
    # to --asof: tax = (2500 - 2000) x rate, net = 2500 - tax
    holding = tax["default_holding"]
    rate = tax[f"{holding}_term_rate"]
    liq_tax = (2500.0 - 2000.0) * rate
    liq_net = 2500.0 - liq_tax
    assert result.outputs["tax"]["summary"]["as_of"] == DECIDED
    assert vti["liq_holding"] == holding
    assert vti["liq_tax"] == pytest.approx(liq_tax)
    assert vti["liq_net"] == pytest.approx(liq_net)
    market, expected = _expected_rates()
    assert result.outputs["market"]["rate"] == pytest.approx(market)
    rates = result.outputs["betas"]["rates"]
    assert rates["VTI"] == pytest.approx(expected["VTI"])
    assert rates["AAPL"] == pytest.approx(expected["AAPL"])
    # hurdle: (mean - E[max null Sharpe] x sd) x periods a year; admissible at the pinned dsr bar
    mean = sum(ACTIVE) / len(ACTIVE)
    sd = (sum((x - mean) ** 2 for x in ACTIVE) / (len(ACTIVE) - 1)) ** 0.5
    hurdle = (mean - EMAX_5 * sd) * hurdle_periods
    assert result.outputs["hurdle"]["rate"] == pytest.approx(hurdle)
    assert result.outputs["hurdle"]["admissible"] is True
    # hold 2500 x (1 + r)^H, its gain over the basis 2000 taxed once at the terminal rate;
    # redeploy: net rate = hurdle - cost, the short-term tax takes its share of a positive rate
    # EVERY year (annual timing, nothing owed at the end), the premium comes off untaxed
    horizon = decide["horizon_years"]
    assert decide["redeploy_tax_timing"] == "annual", "this test works the annual path by hand"
    hold_value = 2500.0 * (1.0 + expected["VTI"]) ** horizon
    hold_net = hold_value - max(hold_value - 2000.0, 0.0) * decide["terminal_tax_rate"]
    net_rate = hurdle - decide["redeploy_cost"]
    redeploy_rate = (net_rate - max(net_rate, 0.0) * decide["redeploy_tax_rate"]
                     - decide["risk_premium"])
    redeploy_value = liq_net * (1.0 + redeploy_rate) ** horizon
    assert vti["horizon_years"] == horizon
    assert vti["hold_value"] == pytest.approx(hold_value)
    assert vti["hold_net"] == pytest.approx(hold_net)
    assert vti["redeploy_value"] == pytest.approx(redeploy_value)
    assert vti["redeploy_terminal_tax"] == 0.0
    assert vti["redeploy_net"] == pytest.approx(redeploy_value)
    assert vti["decision"] == ("exit" if redeploy_value > hold_net else "hold")
    summary = result.outputs["decide"]["summary"]
    assert (summary["hold_return_source"], summary["redeploy_return_source"]) == ("wired", "wired")
    assert summary["redeploy_tax_timing"] == decide["redeploy_tax_timing"]
    written = result.outputs["decisions"]["path"]
    assert os.path.dirname(os.path.abspath(written)) == str(root / "decisions")
    with open(written) as fh:
        lines = fh.read().splitlines()
    assert len(lines) == 2
    assert all(json.loads(line)["run_id"] == os.path.basename(result.run_dir) for line in lines)


def _acquire_decisions(root, run_name):
    _cli(root, "register-source", "decisions", "--root", "./ob",
         "--catalog-source", "decisions-src", "--connector", "localtables",
         "--config", json.dumps({"path": str(root / "decisions"), "layout": "file",
                                 "effective_field": "as_of"}),
         "--activate")
    _cli(root, "acquire", "--root", "./ob", "--source", "decisions",
         "--stream", run_name, "--mode", "backfill")


def test_outcomes_score_the_decision_run_a_year_later(root, monkeypatch):
    monkeypatch.chdir(root)
    decided = _decide(root)
    run_name = os.path.basename(decided.run_dir)
    _acquire_decisions(root, run_name)
    document = _load(OUTCOMES_DOC, str(root), decisions={"stream": run_name},
                     later={"where": _shipped_where(OUTCOMES_DOC, "later", LATER_DAY)})
    result = run_document(document, asof=LATER_DAY, journal=False)
    assert result.state == "ran", (result.state, result.error)
    # the shipped later filter keeps the equity longs and drops the short XYZ and the option
    assert {row["symbol"] for row in result.outputs["later"]["records"]} == {"VTI", "AAPL"}
    rows = {row["symbol"]: row for row in result.outputs["outcomes"]["records"]}
    assert set(rows) == {"VTI", "AAPL"}
    assert rows["VTI"]["account"] == ACCOUNT
    assert rows["VTI"]["realized_growth"] == pytest.approx(1.1)
    assert rows["AAPL"]["realized_growth"] == pytest.approx(0.9)
    # total growth a year is 1 + the VTI hold rate; the forecast's income component (the
    # outcomes document's income_rate) is removed -> price growth a year, compounded for the
    # 365 days elapsed (365 / 365.25 of a year)
    decided_vti = {r["symbol"]: r for r in decided.outputs["decide"]["records"]}["VTI"]
    income = _shipped(OUTCOMES_DOC, "outcomes")["income_rate"]
    horizon = _shipped(RUN_DOC, "decide")["horizon_years"]
    _, expected = _expected_rates()
    elapsed = 365 / 365.25
    total = (decided_vti["hold_value"] / 2500.0) ** (1 / horizon)
    assert total == pytest.approx(1.0 + expected["VTI"])
    assert rows["VTI"]["projected_growth"] == pytest.approx((1.0 + expected["VTI"] - income) ** elapsed)
    assert result.outputs["outcomes"]["summary"]["unmatched"] == []
    assert os.path.isfile(result.outputs["scored"]["path"])


def test_an_undecided_zero_quantity_row_never_refuses_the_scoring_run(root, monkeypatch):
    """Even with the later filter reduced to the date alone, the short XYZ (zero long quantity)
    and the option the decision never covered are not scored and so cannot stop the run."""
    monkeypatch.chdir(root)
    decided = _decide(root)
    run_name = os.path.basename(decided.run_dir)
    _acquire_decisions(root, run_name)
    document = _load(OUTCOMES_DOC, str(root), decisions={"stream": run_name},
                     later={"where": [{"field": "as_of", "op": "==", "value": LATER_DAY}]})
    result = run_document(document, asof=LATER_DAY, journal=False)
    assert result.state == "ran", (result.state, result.error)
    assert len(result.outputs["later"]["records"]) == 4
    assert {row["symbol"] for row in result.outputs["outcomes"]["records"]} == {"VTI", "AAPL"}
