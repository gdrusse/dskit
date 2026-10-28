"""Both documents end to end, in their SHIPPED shape, on a synthetic onboarding root.

One ``positions`` stream holds two snapshots a year apart; the decision run
filters to the earlier day and the scoring run to the later one, exactly as
the documents are written. A ``trades`` stream holds the purchases the lots are
rebuilt from: VTI bought twice (6 shares in 2023, long at the decision; 4 shares
three months before it, short), AAPL once, and a later VTI buy the decision day
does not see. The onboarding root is built the way the owner will build it — the
CLI verbs, from the child root — with ``localfiles`` standing in for
``schwab_positions`` and ``schwab_transactions`` (the observations kind reads the
root, never the connector). The expectations are worked by hand in this file from
the rates the loaded documents declare, never from literals, so an owner replacing
a placeholder keeps the suite green. With the placeholders shipped on 2026-10-27
the same arithmetic gives, per lot: the old VTI lot 1500 over a basis of 1080, long,
tax 420 x .238 = 99.96; the young VTI lot 1000 over 920, short, tax 80 x .408 =
32.64; AAPL 600 over 450, long, 35.70; the second account's VTI lot 1250 over 952.5
(1524 paid x 5/8 left), long, 297.5 x .238 = 70.805. The first account's VTI pays
132.60 in all, where the aggregate row (all long by default) paid 119.
"""

import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import date, timedelta

import pytest

from dskit.pipeline import OutputsConfig, run_document
from dskit.pipeline.document import load_document
from dskit.pipeline.liquidation import LiquidationTax
from dskit.pipeline.node import NodeContext
from dskit.pipeline.tax_lots import LotPositions, TaxLots

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_DOC = os.path.join(CHILD_ROOT, "configs", "run-hold-vs-redeploy.json")
OUTCOMES_DOC = os.path.join(CHILD_ROOT, "configs", "run-decision-outcomes.json")

DECIDED, LATER_DAY = "2025-10-27", "2026-10-27"
ACCOUNT = "F00DBEEFCAFE4C0DE"
ACCOUNT2 = "C0FFEE0DDBA11A2"           # a second account holding the same symbol

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
    # the second account: 5 VTI at the same 250 a share, average price 952.5 / 5
    {"account": ACCOUNT2, "symbol": "VTI", "asset_type": "EQUITY", "as_of": DECIDED,
     "long_quantity": 5.0, "short_quantity": 0.0, "average_price": 190.5,
     "cost_basis": 952.5, "market_value": 1250.0},
]
# the purchases: VTI 6 sh in 2023 (basis 1080) and 4 sh in July 2025 (920) are the 10 shares and
# 2000 of basis the decision snapshot holds; AAPL 3 sh (450, a null amount: the transaction
# carried several items, so the basis is quantity x price); the 10 VTI shares bought in 2026 are
# the later snapshot's (4500 of basis), after the decision day, so its lots never see them.
# The second account bought 8 VTI for 1524 (8 x 190 = 1520 plus a 4.00 commission) and sold 3:
# FIFO leaves 5 sh with basis 1524 x 5/8 = 952.5, where quantity x price would say 950.
# Every trade carries the `time` the connector emits, which orders a day beside the id.
TRADES = [
    {"account": ACCOUNT, "transaction_id": 1001, "trade_date": "2023-06-15",
     "time": "2023-06-15T13:31:00+0000", "symbol": "VTI",
     "asset_type": "EQUITY", "quantity": 6.0, "price": 180.0, "amount": -1080.0,
     "position_effect": "OPENING"},
    {"account": ACCOUNT, "transaction_id": 1002, "trade_date": "2025-07-28",
     "time": "2025-07-28T14:02:00+0000", "symbol": "VTI",
     "asset_type": "EQUITY", "quantity": 4.0, "price": 230.0, "amount": -920.0,
     "position_effect": "OPENING"},
    {"account": ACCOUNT, "transaction_id": 1003, "trade_date": "2024-03-01",
     "time": "2024-03-01T15:45:00+0000", "symbol": "AAPL",
     "asset_type": "EQUITY", "quantity": 3.0, "price": 150.0, "amount": None,
     "position_effect": "OPENING"},
    {"account": ACCOUNT, "transaction_id": 1004, "trade_date": "2026-03-02",
     "time": "2026-03-02T14:10:00+0000", "symbol": "VTI",
     "asset_type": "EQUITY", "quantity": 10.0, "price": 250.0, "amount": -2500.0,
     "position_effect": "OPENING"},
    {"account": ACCOUNT2, "transaction_id": 1005, "trade_date": "2024-02-01",
     "time": "2024-02-01T15:10:00+0000", "symbol": "VTI",
     "asset_type": "EQUITY", "quantity": 8.0, "price": 190.0, "amount": -1524.0,
     "position_effect": "OPENING"},
    {"account": ACCOUNT2, "transaction_id": 1006, "trade_date": "2025-03-03",
     "time": "2025-03-03T16:20:00+0000", "symbol": "VTI",
     "asset_type": "EQUITY", "quantity": -3.0, "price": 215.0, "amount": 645.0,
     "position_effect": "CLOSING"},
]
OLD_VTI = f"{ACCOUNT}:VTI:2023-06-15:1"
YOUNG_VTI = f"{ACCOUNT}:VTI:2025-07-28:1"
AAPL_LOT = f"{ACCOUNT}:AAPL:2024-03-01:1"
A2_VTI = f"{ACCOUNT2}:VTI:2024-02-01:1"
# per lot on the decision day: (symbol, value at the snapshot's 250 / 200 a share, basis, class);
# the second account's lot is 5 x 250 over 952.5, acquired 633 days before the decision
WORKED_LOTS = {OLD_VTI: ("VTI", 1500.0, 1080.0, "long"),
               YOUNG_VTI: ("VTI", 1000.0, 920.0, "short"),
               AAPL_LOT: ("AAPL", 600.0, 450.0, "long"),
               A2_VTI: ("VTI", 1250.0, 952.5, "long")}

# a year later (today, so the snapshot is not a forecast): VTI doubled its share count and its
# price rose 10%; AAPL fell 10%; the short XYZ (zero long quantity) and the option are still
# there, undecided, exactly the rows a real later pull carries
LATER = [
    {**THEN[0], "as_of": LATER_DAY, "long_quantity": 20.0, "market_value": 5500.0,
     "cost_basis": 4500.0},
    {**THEN[1], "as_of": LATER_DAY, "market_value": 540.0},
    {**THEN[2], "as_of": LATER_DAY},
    {**THEN[3], "as_of": LATER_DAY},
    # the second account's VTI rose 4% (1300 / 5 = 260 against 250), the first account's 10%
    {**THEN[4], "as_of": LATER_DAY, "market_value": 1300.0},
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


def _build(tmp_path, trades):
    """An onboarding root under ``tmp_path/ob``: two positions snapshots, the trades, the active
    series."""
    data, active, history = tmp_path / "data", tmp_path / "active", tmp_path / "history"
    data.mkdir()
    active.mkdir()
    history.mkdir()
    _jsonl(data / "positions.jsonl", THEN + LATER)
    _jsonl(history / "trades.jsonl", trades)
    _jsonl(active / "returns.jsonl", [
        {"period": f"2025-W{i + 1:02d}", "net_return": v,
         "day": f"2025-{(i // 4) + 1:02d}-{(i % 4) * 7 + 1:02d}"}
        for i, v in enumerate(ACTIVE)])
    for name in ("decisions", "outcomes", "summary"):
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
    _cli(tmp_path, "register-source", "schwab_trades", "--root", "./ob",
         "--catalog-source", "schwab-trades-src", "--connector", "localfiles",
         "--config", json.dumps({"path": str(history), "effective_field": "trade_date"}),
         "--activate")
    _cli(tmp_path, "acquire", "--root", "./ob", "--source", "schwab_trades",
         "--stream", "trades", "--mode", "backfill")
    return tmp_path


@pytest.fixture
def root(tmp_path):
    """The shipped history: every lot the decision snapshot holds was bought inside it."""
    return _build(tmp_path, TRADES)


def _load(path, run_root, _edit=None, **overrides):
    """Load a document, override run_root, and set the dated/named values a run needs.
    ``_edit`` is applied to the raw document first (a README recipe's own edits)."""
    with open(path) as fh:
        raw = json.load(fh)
    if _edit is not None:
        _edit(raw)
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


def _decide(root, expect="ran", **overrides):
    # twelve weekly returns stand in for a year: the shipped floor is 52 (one year), and the
    # E[max null Sharpe] worked below is for five trials of this series
    params = {"snapshot": {"where": _shipped_where(RUN_DOC, "snapshot", DECIDED)},
              "lots": {"as_of": DECIDED}, "tax": {"as_of": DECIDED}, "hurdle": HURDLE_PINS}
    for node, over in overrides.items():
        params[node] = {**params.get(node, {}), **over}
    document = _load(RUN_DOC, str(root), **params)
    result = run_document(document, asof=DECIDED, journal=False)
    assert result.state == expect, (result.state, result.error)
    return result


def test_documents_are_valid_and_wire_their_stages():
    decide = load_document(RUN_DOC)
    assert list(decide.pipeline) == [
        "positions", "snapshot", "trades", "lots", "lot_positions", "tax", "market", "betas",
        "active_returns", "hurdle", "decide", "decisions", "summary", "summaries"]
    assert decide.pipeline["summary"].inputs == {"records": "$decide.records",
                                                 "betas": "$betas.betas"}
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
    assert outcomes.pipeline["outcomes"].params["key_fields"] == ["account", "symbol", "lot_id"]
    assert outcomes.pipeline["outcomes"].params["match_fields"] == ["account", "symbol"]
    assert decide.hash and outcomes.hash


def test_hold_vs_redeploy_end_to_end(root, monkeypatch):
    monkeypatch.chdir(root)
    result = _decide(root)
    tax, decide = _shipped(RUN_DOC, "tax"), _shipped(RUN_DOC, "decide")
    hurdle_periods = _shipped(RUN_DOC, "hurdle")["periods_per_year"]
    # the lots: the 2026 buy is after the decision day; all three holdings (two accounts' VTI
    # and one AAPL) reconcile share for share; AAPL's null amount fell back to quantity x price
    lots = result.outputs["lots"]["summary"]
    assert (lots["lots"], lots["keys"], lots["after_as_of"], lots["incomplete"]) == (4, 3, 1, [])
    assert lots["amount_fallbacks"] == 1
    joined = result.outputs["lot_positions"]["summary"]
    assert joined["fallback"] == [] and joined["unpositioned"] == []
    assert [r["basis_gap"] for r in joined["reconciled"]] == [0.0, 0.0, 0.0]
    assert joined["basis_gap_max"] == 0.0
    rows = {row["lot_id"]: row for row in result.outputs["decide"]["records"]}
    assert set(rows) == set(WORKED_LOTS), "one row per lot, equities only, no short XYZ"
    # each lot by its own holding period, measured to the snapshot day: the 2023 VTI lot and
    # the AAPL lot are long, the July 2025 VTI lot short (91 days); tax = gain x its rate
    assert result.outputs["tax"]["summary"]["as_of"] == DECIDED
    for lot_id, (symbol, value, basis, holding) in WORKED_LOTS.items():
        row = rows[lot_id]
        assert (row["symbol"], row["market_value"], row["cost_basis"]) == (symbol, value, basis)
        assert row["liq_holding"] == holding
        assert row["liq_tax"] == pytest.approx((value - basis) * tax[f"{holding}_term_rate"])
        assert row["liq_net"] == pytest.approx(value - row["liq_tax"])
    assert rows[OLD_VTI]["acquired"] == "2023-06-15" and rows[YOUNG_VTI]["acquired"] == "2025-07-28"
    # the second account's lot is its own: FIFO took the 3 sold from ITS 8 shares, and the 4.00
    # commission is in the basis (1524 x 5/8 = 952.5, where 5 x 190 = 950 would leave it out)
    assert rows[A2_VTI]["account"] == ACCOUNT2 and rows[A2_VTI]["acquired"] == "2024-02-01"
    assert (rows[A2_VTI]["long_quantity"], rows[A2_VTI]["cost_basis"]) == (5.0, 952.5)
    assert rows[A2_VTI]["liq_tax"] == pytest.approx(
        (1250.0 - 952.5) * tax["long_term_rate"])                 # 297.5 x .238 = 70.805 shipped
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
    # per lot: hold value x (1 + r)^H, its gain over the LOT's basis taxed once at the terminal
    # rate; redeploy the lot's own net: net rate = hurdle - cost, the short-term tax takes its
    # share of a positive rate EVERY year (annual timing), the premium comes off untaxed
    horizon = decide["horizon_years"]
    assert decide["redeploy_tax_timing"] == "annual", "this test works the annual path by hand"
    net_rate = hurdle - decide["redeploy_cost"]
    redeploy_rate = (net_rate - max(net_rate, 0.0) * decide["redeploy_tax_rate"]
                     - decide["risk_premium"])
    for lot_id, (symbol, value, basis, holding) in WORKED_LOTS.items():
        row = rows[lot_id]
        hold_value = value * (1.0 + expected[symbol]) ** horizon
        hold_net = hold_value - max(hold_value - basis, 0.0) * decide["terminal_tax_rate"]
        redeploy_value = row["liq_net"] * (1.0 + redeploy_rate) ** horizon
        assert row["horizon_years"] == horizon
        assert row["hold_value"] == pytest.approx(hold_value)
        assert row["hold_net"] == pytest.approx(hold_net)
        assert row["redeploy_value"] == pytest.approx(redeploy_value)
        assert row["redeploy_terminal_tax"] == 0.0
        assert row["redeploy_net"] == pytest.approx(redeploy_value)
        assert row["decision"] == ("exit" if redeploy_value > hold_net else "hold")
    summary = result.outputs["decide"]["summary"]
    assert (summary["hold_return_source"], summary["redeploy_return_source"]) == ("wired", "wired")
    assert summary["redeploy_tax_timing"] == decide["redeploy_tax_timing"]
    written = result.outputs["decisions"]["path"]
    assert os.path.dirname(os.path.abspath(written)) == str(root / "decisions")
    with open(written) as fh:
        lines = [json.loads(line) for line in fh.read().splitlines()]
    assert sorted(line["lot_id"] for line in lines) == sorted(WORKED_LOTS)
    assert all(line["run_id"] == os.path.basename(result.run_dir) for line in lines)


def test_a_holding_bought_before_the_history_refuses_unless_allowed(tmp_path, monkeypatch):
    """AAPL was bought before the trades begin. The shipped document refuses by holding at the
    reconciliation; declared allow_incomplete, AAPL passes through as its aggregate row with no
    date, so the tax's default_holding applies to it exactly as before lots existed, while VTI
    keeps its two lots. An AAPL sale with no purchase behind it refuses at the lots instead."""
    root = _build(tmp_path, [t for t in TRADES if t["symbol"] != "AAPL"])
    monkeypatch.chdir(root)
    refused = _decide(root, expect="error")
    assert refused.halted_at == "lot_positions"
    assert f"('{ACCOUNT}', 'AAPL') has no lots" in refused.error
    allowed = _decide(root, lots={"allow_incomplete": True},
                      lot_positions={"allow_incomplete": True})
    rows = {row["lot_id"]: row for row in allowed.outputs["decide"]["records"]}
    assert set(rows) == {OLD_VTI, YOUNG_VTI, f"{ACCOUNT}:AAPL:aggregate", A2_VTI}
    aapl = rows[f"{ACCOUNT}:AAPL:aggregate"]
    tax = _shipped(RUN_DOC, "tax")
    assert aapl["acquired"] is None and aapl["liq_holding"] == tax["default_holding"]
    assert (aapl["long_quantity"], aapl["market_value"], aapl["cost_basis"]) == (3.0, 600.0, 450.0)
    assert aapl["liq_tax"] == pytest.approx(150.0 * tax[f"{tax['default_holding']}_term_rate"])
    assert allowed.outputs["lot_positions"]["summary"]["fallback"] == [
        {"account": ACCOUNT, "symbol": "AAPL", "reason": "no lots"}]
    assert rows[YOUNG_VTI]["liq_holding"] == "short"


def test_a_sale_the_history_never_bought_refuses_at_the_lots(tmp_path, monkeypatch):
    sale = {**TRADES[2], "transaction_id": 999, "trade_date": "2024-01-02",
            "time": "2024-01-02T14:00:00+0000", "quantity": -1.0, "amount": 150.0,
            "position_effect": "CLOSING"}
    root = _build(tmp_path, TRADES + [sale])
    monkeypatch.chdir(root)
    refused = _decide(root, expect="error")
    assert refused.halted_at == "lots"
    assert f"sells 1.0 of '{ACCOUNT}:AAPL' on 2024-01-02 but 0.0 share(s) are open" in refused.error


def _break_even(net, hold_net):
    """``{"break_even": R}``: the redeploy return at which ``net`` redeployed ends at ``hold_net``.
    Work backwards: g = (hold_net / net) ** (1 / H) - 1 is the after-tax, after-premium rate the
    redeploy path needs; the annual tax takes t of a positive net rate, so a positive
    g + premium needs a net rate of (g + premium) / (1 - t); R adds the cost back."""
    decide = _shipped(RUN_DOC, "decide")
    needed = (hold_net / net) ** (1.0 / decide["horizon_years"]) - 1.0 + decide["risk_premium"]
    if needed > 0.0:
        needed /= 1.0 - decide["redeploy_tax_rate"]
    return {"break_even": decide["redeploy_cost"] + needed}


def _worked(value, basis, holding, hold_rate, hurdle):
    """One lot's summary numbers, worked by hand from the shipped decision document's
    declarations (annual redeploy timing, as the e2e test works it): the liquidation net at the
    lot's own holding class, both terminal nets, the redeploy return at which they tie and the
    after-tax opportunity cost."""
    tax, decide = _shipped(RUN_DOC, "tax"), _shipped(RUN_DOC, "decide")
    years, cost = decide["horizon_years"], decide["redeploy_cost"]
    premium, redeploy_tax = decide["risk_premium"], decide["redeploy_tax_rate"]
    liq_tax = max(value - basis, 0.0) * tax[f"{holding}_term_rate"]
    net = value - liq_tax
    hold_value = value * (1.0 + hold_rate) ** years
    hold_net = hold_value - max(hold_value - basis, 0.0) * decide["terminal_tax_rate"]
    net_rate = hurdle - cost
    rate = net_rate - max(net_rate, 0.0) * redeploy_tax - premium
    return {"liq_tax": liq_tax, "net": net, "hold_net": hold_net,
            "redeploy_net": net * (1.0 + rate) ** years, **_break_even(net, hold_net)}


def test_position_summary_end_to_end(root, monkeypatch):
    """The few numbers per lot, worked by hand from the same rows and the shipped declarations.
    With the placeholders of 2026-10-27: the old VTI lot 1500 (basis 1080, long, tax 99.96), the
    young VTI lot 1000 (basis 920, short, tax 32.64), AAPL 600 (basis 450, long, tax 35.70) and
    the second account's VTI lot 1250 (basis 952.5, long, tax 70.805): 4350 of value."""
    monkeypatch.chdir(root)
    result = _decide(root)
    decide = _shipped(RUN_DOC, "decide")
    assert decide["redeploy_tax_timing"] == "annual", "this test works the annual path by hand"
    betas = _shipped(RUN_DOC, "betas")["betas"]
    hurdle = result.outputs["hurdle"]["rate"]    # worked by hand in the end-to-end test above
    rates = result.outputs["betas"]["rates"]
    years = decide["horizon_years"]
    rows = {row["lot_id"]: row for row in result.outputs["summary"]["records"]}
    decided = {row["lot_id"]: row for row in result.outputs["decide"]["records"]}
    assert set(rows) == set(WORKED_LOTS)
    worked = {lot_id: _worked(value, basis, holding, rates[symbol], hurdle)
              for lot_id, (symbol, value, basis, holding) in WORKED_LOTS.items()}
    old = rows[OLD_VTI]
    assert list(old)[:3] == ["account", "symbol", "lot_id"] and old["account"] == ACCOUNT
    assert [rows[lot]["value"] for lot in (OLD_VTI, YOUNG_VTI, AAPL_LOT, A2_VTI)] == [
        1500.0, 1000.0, 600.0, 1250.0]
    total_value = 1500.0 + 1000.0 + 600.0 + 1250.0
    assert old["weight"] == pytest.approx(1500.0 / total_value)
    assert old["horizon_years"] == years
    for lot_id, (symbol, value, basis, _) in WORKED_LOTS.items():
        row = rows[lot_id]
        # expected return a year is the beta rate itself: (hold_value / value) ** (1 / H) - 1
        assert row["expected_return"] == pytest.approx(rates[symbol])
        assert row["beta"] == betas[symbol]                 # wired, never restated
        assert row["volatility"] is None, "no price history is wired yet"
        assert row["embedded_gain"] == pytest.approx((value - basis) / value)
        assert row["tax_cost"] == pytest.approx(worked[lot_id]["liq_tax"] / value)
        assert row["break_even_return"] == pytest.approx(worked[lot_id]["break_even"])
        assert row["opportunity_cost"] == pytest.approx(
            (worked[lot_id]["redeploy_net"] / worked[lot_id]["hold_net"]) ** (1.0 / years) - 1.0)
        assert row["decision"] == decided[lot_id]["decision"]
        # the decision and the break-even agree: exit exactly when the hurdle clears it
        assert (row["decision"] == "exit") == (hurdle > row["break_even_return"])
    # the break-even inverts the comparison decide made: redeploying at it ties the hold path
    net_rate = old["break_even_return"] - decide["redeploy_cost"]
    tied = (net_rate - max(net_rate, 0.0) * decide["redeploy_tax_rate"]
            - decide["risk_premium"])
    assert worked[OLD_VTI]["net"] * (1.0 + tied) ** years == pytest.approx(
        worked[OLD_VTI]["hold_net"])
    # portfolio: weights over 4350; concentration merges the three VTI lots (two accounts) by
    # symbol; totals' break-even and opportunity cost are exact because every row shares one
    # redeploy rate. `positions` and the exit / hold counts count LOTS, not holdings.
    m = result.outputs["summary"]["metrics"]
    weights = {"VTI": 3750.0 / total_value, "AAPL": 600.0 / total_value}
    assert (m["positions"], m["total_value"]) == (4, total_value)
    assert m["expected_return"] == pytest.approx(sum(weights[s] * rates[s] for s in weights))
    assert m["beta"] == pytest.approx(sum(weights[s] * betas[s] for s in weights))
    assert m["hhi"] == pytest.approx(sum(w ** 2 for w in weights.values()))
    assert m["effective_holdings"] == pytest.approx(1.0 / m["hhi"])
    assert (m["top_symbol"], m["top_weight"]) == ("VTI", pytest.approx(weights["VTI"]))
    assert m["tax_cost"] == pytest.approx(
        sum(w["liq_tax"] for w in worked.values()) / total_value)   # (99.96 + 32.64 + 35.70 + 70.805) / 4350
    total = {k: sum(w[k] for w in worked.values()) for k in ("net", "hold_net", "redeploy_net")}
    assert m["opportunity_cost"] == pytest.approx(
        (total["redeploy_net"] / total["hold_net"]) ** (1.0 / years) - 1.0)
    assert m["break_even_return"] == pytest.approx(
        _break_even(total["net"], total["hold_net"])["break_even"])
    assert m["weighted_volatility"] is None
    assert (m["exit_positions"], m["hold_positions"]) == (
        sum(r["decision"] == "exit" for r in decided.values()),
        sum(r["decision"] == "hold" for r in decided.values()))
    written = result.outputs["summaries"]["path"]
    assert os.path.dirname(os.path.abspath(written)) == str(root / "summary")
    with open(written) as fh:
        lines = [json.loads(line) for line in fh.read().splitlines()]
    # the file holds exactly the summary node's rows, field for field, plus the run that wrote them
    run_id = os.path.basename(result.run_dir)
    assert lines == [{**row, "run_id": run_id} for row in result.outputs["summary"]["records"]]
    with open(os.path.join(result.run_dir, "carry.json")) as fh:
        assert json.load(fh)["summary"]["metrics"]["hhi"] == pytest.approx(m["hhi"])


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
    rows = {row["lot_id"]: row for row in result.outputs["outcomes"]["records"]}
    assert set(rows) == set(WORKED_LOTS), "every decided lot is scored against its holding"
    assert rows[OLD_VTI]["account"] == ACCOUNT and rows[A2_VTI]["account"] == ACCOUNT2
    # per share: both first-account VTI lots see 275 / 250 (the 2026 buy doubled the count, not
    # the price); the second account's lot is matched to ITS later row, 260 / 250
    assert rows[OLD_VTI]["realized_growth"] == pytest.approx(1.1)
    assert rows[YOUNG_VTI]["realized_growth"] == pytest.approx(1.1)
    assert rows[A2_VTI]["realized_growth"] == pytest.approx(1.04)
    assert rows[AAPL_LOT]["realized_growth"] == pytest.approx(0.9)
    # total growth a year is 1 + the VTI hold rate; the forecast's income component (the
    # outcomes document's income_rate) is removed -> price growth a year, compounded for the
    # 365 days elapsed (365 / 365.25 of a year); a lot's value and hold value scale together
    decided_rows = {r["lot_id"]: r for r in decided.outputs["decide"]["records"]}
    income = _shipped(OUTCOMES_DOC, "outcomes")["income_rate"]
    horizon = _shipped(RUN_DOC, "decide")["horizon_years"]
    _, expected = _expected_rates()
    elapsed = 365 / 365.25
    for lot_id in (OLD_VTI, YOUNG_VTI, A2_VTI):
        total = (decided_rows[lot_id]["hold_value"] / decided_rows[lot_id]["market_value"]) ** (
            1 / horizon)
        assert total == pytest.approx(1.0 + expected["VTI"])
        assert rows[lot_id]["projected_growth"] == pytest.approx(
            (1.0 + expected["VTI"] - income) ** elapsed)
        assert rows[lot_id]["decision"] == decided_rows[lot_id]["decision"]
    summary = result.outputs["outcomes"]["summary"]
    assert summary["unmatched"] == []
    # four lots over three holdings: the two first-account VTI lots share one holding. Surprise =
    # realized - projected; every VTI lot projects the same growth
    vti_projected = (1.0 + expected["VTI"] - income) ** elapsed
    aapl_projected = (1.0 + expected["AAPL"] - income) ** elapsed
    surprise = {"a1_vti": 1.1 - vti_projected, "aapl": 0.9 - aapl_projected,
                "a2_vti": 1.04 - vti_projected}
    assert (summary["scored"], summary["holdings"]) == (4, 3)
    assert summary["mean_surprise"] == pytest.approx(
        (2 * surprise["a1_vti"] + surprise["aapl"] + surprise["a2_vti"]) / 4)
    assert summary["holding_mean_surprise"] == pytest.approx(sum(surprise.values()) / 3)
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
    assert len(result.outputs["later"]["records"]) == 5
    assert {row["symbol"] for row in result.outputs["outcomes"]["records"]} == {"VTI", "AAPL"}


def test_a_sale_the_other_accounts_shares_could_cover_refuses_at_the_lots(tmp_path, monkeypatch):
    """The second account sells 9 VTI in February 2025 with 8 open in it; the first account's 6
    shares would cover the 1 short if lots were pooled by symbol. They are not: the sale refuses
    by account, at the lots."""
    sale = {**TRADES[4], "transaction_id": 1007, "trade_date": "2025-02-01",
            "time": "2025-02-01T16:00:00+0000", "quantity": -9.0, "price": 200.0,
            "amount": 1800.0, "position_effect": "CLOSING"}
    root = _build(tmp_path, TRADES + [sale])
    monkeypatch.chdir(root)
    refused = _decide(root, expect="error")
    assert refused.halted_at == "lots"
    assert (f"sells 9.0 of '{ACCOUNT2}:VTI' on 2025-02-01 but 8.0 share(s) are open "
            "(oversold by 1.0)") in refused.error


def test_lots_a_day_either_side_of_the_long_term_threshold_by_the_documents_days():
    """The shipped lots, join and tax nodes on two purchases of one holding: one 365 days before
    the decision day, one 366. The document's long_term_days (366: MORE than a year) makes the
    first short and the second long, each taxed at its class's rate on its own gain."""
    ctx = NodeContext(name="lots", asof=DECIDED, run_dir=".")
    # the shipped nodes, the decision day set the way _decide sets it (lots.as_of and tax.as_of)
    lots_p, join_p, tax_p = ({**_shipped(RUN_DOC, name), **extra} for name, extra in (
        ("lots", {"as_of": DECIDED}), ("lot_positions", {}), ("tax", {"as_of": DECIDED})))
    day = date.fromisoformat(DECIDED)
    held = {365: day - timedelta(days=365), 366: day - timedelta(days=366)}
    assert (held[365].isoformat(), held[366].isoformat()) == ("2024-10-27", "2024-10-26"), (
        "no 29 February falls inside either span")
    assert tax_p["long_term_days"] == 366, "a lot is long-term when held MORE than a year"
    trades = [{"account": ACCOUNT, "transaction_id": 1 + i, "trade_date": acquired.isoformat(),
               "time": f"{acquired.isoformat()}T14:30:00+0000", "symbol": "VTI",
               "asset_type": "EQUITY", "quantity": 1.0, "price": 100.0, "amount": -100.0,
               "position_effect": "OPENING"} for i, acquired in enumerate(held.values())]
    snapshot = [{**THEN[0], "long_quantity": 2.0, "cost_basis": 200.0, "market_value": 500.0}]
    lots = TaxLots("lots", lots_p).run(ctx, {"records": trades})["records"]
    joined = LotPositions("lot_positions", join_p).run(
        ctx, {"lots": lots, "positions": snapshot})["records"]
    taxed = LiquidationTax("tax", tax_p).run(ctx, {"records": joined})["records"]
    # 250 a share (500 / 2) over a 100 basis: a 150 gain on each lot
    assert {r["acquired"]: (r["liq_holding"], r["liq_gain"]) for r in taxed} == {
        held[365].isoformat(): ("short", 150.0), held[366].isoformat(): ("long", 150.0)}
    by_class = {r["liq_holding"]: r for r in taxed}
    assert by_class["short"]["liq_tax"] == pytest.approx(150.0 * tax_p["short_term_rate"])
    assert by_class["long"]["liq_tax"] == pytest.approx(150.0 * tax_p["long_term_rate"])


def _aggregate_decisions(raw):
    """README "To go back to the aggregate path", decision document: delete the trades, lots and
    lot_positions nodes, point tax.inputs.records at $snapshot.records, drop tax.acquired_field,
    and key the summary on account + symbol (there is no lot_id to key on)."""
    nodes = raw["pipeline"]
    for name in ("trades", "lots", "lot_positions"):
        del nodes[name]
    nodes["tax"]["inputs"]["records"] = "$snapshot.records"
    del nodes["tax"]["params"]["acquired_field"]
    nodes["summary"]["params"]["key_fields"] = ["account", "symbol"]


def _aggregate_outcomes(raw):
    """The same recipe, scoring document: decisions and outcomes keyed on account + symbol,
    match_fields dropped (it would equal key_fields)."""
    nodes = raw["pipeline"]
    nodes["decisions"]["params"]["key_fields"] = ["account", "symbol", "as_of"]
    nodes["outcomes"]["params"]["key_fields"] = ["account", "symbol"]
    del nodes["outcomes"]["params"]["match_fields"]


def test_the_readmes_aggregate_path_recipe_runs_both_documents(root, monkeypatch):
    """The recipe as the README states it, applied to the shipped documents: no lots, one row
    per account x symbol, each taxed as default_holding (the first account's VTI at the long rate
    on 2500 over 2000: 119), and the same decisions scored a year later per holding."""
    monkeypatch.chdir(root)
    tax = _shipped(RUN_DOC, "tax")
    document = _load(RUN_DOC, str(root), _edit=_aggregate_decisions,
                     snapshot={"where": _shipped_where(RUN_DOC, "snapshot", DECIDED)},
                     tax={"as_of": DECIDED}, hurdle=HURDLE_PINS)
    decided = run_document(document, asof=DECIDED, journal=False)
    assert decided.state == "ran", (decided.state, decided.error)
    rows = {(r["account"], r["symbol"]): r for r in decided.outputs["decide"]["records"]}
    assert set(rows) == {(ACCOUNT, "VTI"), (ACCOUNT, "AAPL"), (ACCOUNT2, "VTI")}
    assert all("lot_id" not in r and "acquired" not in r for r in rows.values())
    rate = tax[f"{tax['default_holding']}_term_rate"]
    assert {key: r["liq_holding"] for key, r in rows.items()} == {
        key: tax["default_holding"] for key in rows}
    assert rows[(ACCOUNT, "VTI")]["liq_tax"] == pytest.approx((2500.0 - 2000.0) * rate)
    assert rows[(ACCOUNT2, "VTI")]["liq_tax"] == pytest.approx((1250.0 - 952.5) * rate)
    summary = {(r["account"], r["symbol"]): r for r in decided.outputs["summary"]["records"]}
    assert set(summary) == set(rows) and "lot_id" not in next(iter(summary.values()))
    # the scoring run, keyed per holding
    run_name = os.path.basename(decided.run_dir)
    _acquire_decisions(root, run_name)
    scoring = _load(OUTCOMES_DOC, str(root), _edit=_aggregate_outcomes,
                    decisions={"stream": run_name},
                    later={"where": _shipped_where(OUTCOMES_DOC, "later", LATER_DAY)})
    scored = run_document(scoring, asof=LATER_DAY, journal=False)
    assert scored.state == "ran", (scored.state, scored.error)
    out = {(r["account"], r["symbol"]): r for r in scored.outputs["outcomes"]["records"]}
    assert set(out) == set(rows)
    assert out[(ACCOUNT, "VTI")]["realized_growth"] == pytest.approx(1.1)
    assert out[(ACCOUNT2, "VTI")]["realized_growth"] == pytest.approx(1.04)
    assert out[(ACCOUNT, "AAPL")]["realized_growth"] == pytest.approx(0.9)
    # one row per holding, so the per-holding and per-row counts agree
    assert (scored.outputs["outcomes"]["summary"]["scored"],
            scored.outputs["outcomes"]["summary"]["holdings"]) == (3, 3)
