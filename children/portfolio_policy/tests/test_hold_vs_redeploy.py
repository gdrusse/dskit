"""Both documents end to end, in their SHIPPED shape, on a synthetic onboarding root.

One ``positions`` stream holds two snapshots a year apart; the decision run
filters to the earlier day and the scoring run to the later one, exactly as
the documents are written. The onboarding root is built the way the owner will
build it — the CLI verbs, from the child root — with ``localfiles`` standing in
for ``schwab_positions`` (the observations kind reads the root, never the
connector). The expectations are worked by hand in this file from the rates the
loaded documents declare, never from literals, so an owner replacing a
placeholder keeps the suite green. With the placeholders shipped on 2026-10-27
and a beta of exactly 1 the same arithmetic gave, for VTI: tax 119, net 2381,
hold_net 2680.58 against redeploy_net 3052.07, so ``exit``.

A daily ``bars`` stream (``localfiles`` standing in for ``schwab_daily``) carries
300 sessions of SPY, VTI and AAPL from a seeded one-factor process (see
``_daily_bars``), ending the Friday before the decision day, as a pull on that
Monday would. The betas that price the hold return and the volatilities in the
summary come from it: checked loosely against the generator's known parameters,
and exactly against a plain-Python recomputation from the written bars.
"""

import json
import math
import os
import random
import subprocess
import sys
from dataclasses import replace
from datetime import date, timedelta

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

# Daily bars from a seeded one-factor process with a known truth. SPY's daily log return is
# MU_M + SIG_M x z; a holding's is BETA x SPY's + SIG_E x z' (z, z' independent standard
# normals from random.Random(SEED), drawn SPY first, then VTI, then AAPL, every session). So a
# holding's beta is BETA and its zero-mean RMS a day is sqrt(BETA^2 (SIG_M^2 + MU_M^2) + SIG_E^2).
SEED = 20251027
MU_M, SIG_M = 0.0004, 0.01
FACTORS = {"VTI": (1.0, 0.002), "AAPL": (1.3, 0.012)}       # symbol: (BETA, SIG_E)
START_CLOSE = {"AAPL": 200.0, "SPY": 500.0, "VTI": 250.0}
SESSIONS = 300
# DECIDED is a Monday; a pull that day has no closed bar for it, so the last session is Friday's
LAST_SESSION = "2025-10-24"
TRADING_DAYS = 252   # the annualization factor of research A0003: trading days a year


def _sessions():
    """SESSIONS weekdays (no holidays: synthetic) ending on LAST_SESSION."""
    days, day = [], date.fromisoformat(LAST_SESSION)
    while len(days) < SESSIONS:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day -= timedelta(days=1)
    return days[::-1]


def _daily_bars():
    """The bar rows, shaped like schwab_daily's: the close repeated as open/high/low."""
    rng = random.Random(SEED)
    closes, rows = dict(START_CLOSE), []
    for index, day in enumerate(_sessions()):
        if index:
            market = MU_M + SIG_M * rng.gauss(0.0, 1.0)
            moves = {"SPY": market}
            for symbol, (beta, sig_e) in FACTORS.items():
                moves[symbol] = beta * market + sig_e * rng.gauss(0.0, 1.0)
            closes = {symbol: close * math.exp(moves[symbol]) for symbol, close in closes.items()}
        rows += [{"symbol": symbol, "ts": day, "open": close, "high": close, "low": close,
                  "close": close, "volume": 1e6, "trade_count": None, "vwap": None}
                 for symbol, close in sorted(closes.items())]
    return rows


def _plain_risk(root, day):
    """Each symbol's risk numbers at its latest session on or before ``day``, recomputed from
    the WRITTEN bars in plain Python (no dskit): the zero-mean RMS of its last ``window`` daily
    log returns x sqrt(252), and the OLS slope of those returns on SPY's over the same sessions,
    Blume-adjusted 0.67 x raw + 0.33 (research A0003). The windows are the document's."""
    features = _shipped(RUN_DOC, "risk_features")
    vol_window = features["realized_vol"]["windows"][0]
    beta_window = features["market_relative"]["beta_window"]
    with open(root / "daily" / "bars.jsonl") as fh:
        bars = [json.loads(line) for line in fh]
    close = {(row["symbol"], row["ts"]): row["close"] for row in bars}
    out = {}
    for symbol in sorted({row["symbol"] for row in bars}):
        days = sorted(ts for (s, ts) in close if s == symbol and ts <= day)

        def returns(name, window, days=days):
            last = days[-(window + 1):]
            return [math.log(close[name, b] / close[name, a]) for a, b in zip(last, last[1:])]

        own = returns(symbol, vol_window)
        daily = math.sqrt(sum(r * r for r in own) / vol_window)
        own, mkt = returns(symbol, beta_window), returns("SPY", beta_window)
        mean_own, mean_mkt = sum(own) / beta_window, sum(mkt) / beta_window
        raw = (sum((o - mean_own) * (m - mean_mkt) for o, m in zip(own, mkt))
               / sum((m - mean_mkt) ** 2 for m in mkt))
        out[symbol] = {"as_of": days[-1], "daily_vol": daily,
                       "annual_vol": daily * math.sqrt(TRADING_DAYS),
                       "beta_raw": raw, "beta": 0.67 * raw + 0.33}
    return out


def _held_betas(root):
    """The adjusted betas of the two decided holdings, from the plain recomputation."""
    risk = _plain_risk(root, DECIDED)
    return {symbol: risk[symbol]["beta"] for symbol in ("VTI", "AAPL")}


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
    """An onboarding root under ``tmp_path/ob``: two positions snapshots, the active series and
    the daily bars."""
    data, active, daily = tmp_path / "data", tmp_path / "active", tmp_path / "daily"
    data.mkdir()
    active.mkdir()
    daily.mkdir()
    _jsonl(data / "positions.jsonl", THEN + LATER)
    _jsonl(daily / "bars.jsonl", _daily_bars())
    _jsonl(active / "returns.jsonl", [
        {"period": f"2025-W{i + 1:02d}", "net_return": v,
         "day": f"2025-{(i // 4) + 1:02d}-{(i % 4) * 7 + 1:02d}"}
        for i, v in enumerate(ACTIVE)])
    for name in ("decisions", "outcomes", "summary", "risk"):
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
    _cli(tmp_path, "register-source", "schwab_daily", "--root", "./ob",
         "--catalog-source", "schwab-daily-src", "--connector", "localfiles",
         "--config", json.dumps({"path": str(daily), "effective_field": "ts"}), "--activate")
    _cli(tmp_path, "acquire", "--root", "./ob", "--source", "schwab_daily",
         "--stream", "bars", "--mode", "backfill")
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


def _shipped_where(path, node, day, field="as_of"):
    """A shipped filter with only its date replaced, so every other clause (equities only,
    longs only) runs exactly as shipped."""
    where = _shipped(path, node)["where"]
    return [{**clause, "value": day} if clause["field"] == field else clause for clause in where]


def _expected_rates(betas):
    """``(market, {symbol: rate})`` worked by hand from the decision document's declarations
    and the given betas: market = yield + growth + inflation + ((fair CAPE / CAPE) ** (1 /
    years) - 1); symbol = risk_free + beta x (market - risk_free)."""
    m, b = _shipped(RUN_DOC, "market"), _shipped(RUN_DOC, "betas")
    market = (m["dividend_yield"] + m["real_growth"] + m["inflation"]
              + (m["fair_cape"] / m["cape"]) ** (1.0 / m["reversion_years"]) - 1.0)
    return market, {symbol: b["risk_free"] + beta * (market - b["risk_free"])
                    for symbol, beta in betas.items()}


def _decide(root, risk_day=DECIDED):
    # twelve weekly returns stand in for a year: the shipped floor is 52 (one year), and the
    # E[max null Sharpe] worked below is for five trials of this series
    document = _load(RUN_DOC, str(root),
                     snapshot={"where": _shipped_where(RUN_DOC, "snapshot", DECIDED)},
                     tax={"as_of": DECIDED}, hurdle=HURDLE_PINS,
                     risk_day={"where": _shipped_where(RUN_DOC, "risk_day", risk_day, "ts")})
    result = run_document(document, asof=DECIDED, journal=False)
    assert result.state == "ran", (result.state, result.error)
    return result


def test_documents_are_valid_and_wire_their_stages():
    decide = load_document(RUN_DOC)
    assert list(decide.pipeline) == [
        "positions", "snapshot", "tax", "market", "bars", "benchmark", "risk_features",
        "risk_day", "risk_latest", "risk", "vols", "risk_betas", "risks", "betas",
        "active_returns", "hurdle", "decide", "decisions", "summary", "summaries"]
    assert decide.pipeline["summary"].inputs == {"records": "$decide.records",
                                                 "betas": "$betas.betas",
                                                 "volatilities": "$vols.table"}
    assert decide.pipeline["betas"].inputs["betas"] == "$risk_betas.table"
    assert decide.pipeline["risk_features"].inputs == {"bars": "$bars.records",
                                                       "benchmark": "$benchmark.records"}
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


def test_risk_inputs_from_daily_bars_end_to_end(root, monkeypatch):
    """Volatility and beta per symbol, from the daily bars: near the generator's truth, and
    exactly the plain-Python recomputation from the written bars; then wired into the hold
    returns and written to risk/<run>.jsonl."""
    monkeypatch.chdir(root)
    result = _decide(root)
    rows = {row["symbol"]: row for row in result.outputs["risk"]["records"]}
    assert list(rows) == ["AAPL", "SPY", "VTI"]
    plain = _plain_risk(root, DECIDED)
    features = _shipped(RUN_DOC, "risk_features")
    for symbol, row in rows.items():
        # the decision day is a Monday with no closed bar yet: the latest session before it
        assert row["as_of"] == plain[symbol]["as_of"] == LAST_SESSION
        for field in ("daily_vol", "annual_vol", "beta_raw", "beta"):
            assert row[field] == pytest.approx(plain[symbol][field], rel=1e-9), (symbol, field)
        assert row["vol_window"] == features["realized_vol"]["windows"][0]
        assert row["beta_window"] == features["market_relative"]["beta_window"]
    # loosely, the generator's known truth (252 returns: about 4.5% relative error on a
    # volatility, 0.01 / 0.08 on VTI's / AAPL's beta, so these bounds are 3 standard errors+)
    truth_daily = {"SPY": math.sqrt(SIG_M ** 2 + MU_M ** 2)}
    for symbol, (beta, sig_e) in FACTORS.items():
        truth_daily[symbol] = math.sqrt(beta ** 2 * (SIG_M ** 2 + MU_M ** 2) + sig_e ** 2)
    for symbol, daily in truth_daily.items():
        # SPY 0.159, VTI 0.162, AAPL 0.281 a year
        assert rows[symbol]["annual_vol"] == pytest.approx(daily * math.sqrt(TRADING_DAYS),
                                                           rel=0.15)
    assert rows["SPY"]["beta_raw"] == pytest.approx(1.0, abs=1e-12)    # SPY on itself
    assert rows["VTI"]["beta_raw"] == pytest.approx(FACTORS["VTI"][0], abs=0.05)
    assert rows["AAPL"]["beta_raw"] == pytest.approx(FACTORS["AAPL"][0], abs=0.25)
    assert rows["AAPL"]["beta"] == pytest.approx(0.67 * rows["AAPL"]["beta_raw"] + 0.33)
    # the adjusted betas price the hold return; the summary sees the same table
    assert result.outputs["betas"]["betas"] == {
        "VTI": pytest.approx(rows["VTI"]["beta"]), "AAPL": pytest.approx(rows["AAPL"]["beta"])}
    assert result.outputs["vols"]["table"] == {s: row["annual_vol"] for s, row in rows.items()}
    written = result.outputs["risks"]["path"]
    assert os.path.dirname(os.path.abspath(written)) == str(root / "risk")
    with open(written) as fh:
        lines = [json.loads(line) for line in fh.read().splitlines()]
    run_id = os.path.basename(result.run_dir)
    assert lines == [{**row, "run_id": run_id} for row in result.outputs["risk"]["records"]]


def test_the_risk_day_reads_no_later_session(root, monkeypatch):
    """A risk day two sessions earlier reads that day's numbers: nothing after it is seen."""
    monkeypatch.chdir(root)
    earlier = _sessions()[-3]
    result = _decide(root, risk_day=earlier)
    rows = {row["symbol"]: row for row in result.outputs["risk"]["records"]}
    plain, latest = _plain_risk(root, earlier), _plain_risk(root, DECIDED)
    for symbol, row in rows.items():
        assert row["as_of"] == earlier
        assert row["annual_vol"] == pytest.approx(plain[symbol]["annual_vol"], rel=1e-9)
        assert row["beta"] == pytest.approx(plain[symbol]["beta"], rel=1e-9)
        assert row["annual_vol"] != pytest.approx(latest[symbol]["annual_vol"], rel=1e-9)


@pytest.mark.parametrize("missing, message", [
    ("AAPL", "no beta for symbol 'AAPL'"),               # the symbols forgot a holding
    ("SPY", "betas input['AAPL'] must be a number, got None"),   # ... or the benchmark
])
def test_a_bars_pull_missing_a_symbol_stops_the_decision_by_name(root, monkeypatch, missing,
                                                                  message):
    """No default beta and no default volatility: what the bars cannot price refuses."""
    monkeypatch.chdir(root)
    lite = root / f"without-{missing}"
    lite.mkdir()
    _jsonl(lite / "bars.jsonl", [row for row in _daily_bars() if row["symbol"] != missing])
    _cli(root, "register-source", "schwab_daily_lite", "--root", "./ob",
         "--catalog-source", "schwab-daily-lite-src", "--connector", "localfiles",
         "--config", json.dumps({"path": str(lite), "effective_field": "ts"}), "--activate")
    _cli(root, "acquire", "--root", "./ob", "--source", "schwab_daily_lite",
         "--stream", "bars", "--mode", "backfill")
    document = _load(RUN_DOC, str(root),
                     snapshot={"where": _shipped_where(RUN_DOC, "snapshot", DECIDED)},
                     tax={"as_of": DECIDED}, hurdle=HURDLE_PINS,
                     bars={"source": "schwab_daily_lite"},
                     risk_day={"where": _shipped_where(RUN_DOC, "risk_day", DECIDED, "ts")})
    result = run_document(document, asof=DECIDED, journal=False)
    assert result.state != "ran"
    assert message in str(result.error)


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
    market, expected = _expected_rates(_held_betas(root))
    assert result.outputs["market"]["rate"] == pytest.approx(market)
    rates = result.outputs["betas"]["rates"]
    assert result.outputs["betas"]["summary"]["betas_source"] == "wired"
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


def _worked(value, basis, hold_rate, hurdle):
    """One holding's summary numbers, worked by hand from the shipped decision document's
    declarations (annual redeploy timing, as the e2e test works it): the liquidation net, both
    terminal nets, the redeploy return at which they tie and the after-tax opportunity cost."""
    tax, decide = _shipped(RUN_DOC, "tax"), _shipped(RUN_DOC, "decide")
    years, cost = decide["horizon_years"], decide["redeploy_cost"]
    premium, redeploy_tax = decide["risk_premium"], decide["redeploy_tax_rate"]
    liq_tax = max(value - basis, 0.0) * tax[f"{tax['default_holding']}_term_rate"]
    net = value - liq_tax
    hold_value = value * (1.0 + hold_rate) ** years
    hold_net = hold_value - max(hold_value - basis, 0.0) * decide["terminal_tax_rate"]
    net_rate = hurdle - cost
    rate = net_rate - max(net_rate, 0.0) * redeploy_tax - premium
    return {"liq_tax": liq_tax, "net": net, "hold_net": hold_net,
            "redeploy_net": net * (1.0 + rate) ** years, **_break_even(net, hold_net)}


def test_position_summary_end_to_end(root, monkeypatch):
    """The few numbers, worked by hand from the same rows and the shipped declarations. With the
    placeholders of 2026-10-27: VTI 2500 (basis 2000, tax 119, net 2381, hold_net 2680.58,
    redeploy_net 3052.07), AAPL 600 (basis 450, tax 35.70, net 564.30), five years."""
    monkeypatch.chdir(root)
    result = _decide(root)
    decide = _shipped(RUN_DOC, "decide")
    assert decide["redeploy_tax_timing"] == "annual", "this test works the annual path by hand"
    risk = _plain_risk(root, DECIDED)
    betas = {symbol: risk[symbol]["beta"] for symbol in ("VTI", "AAPL")}
    vols = {symbol: risk[symbol]["annual_vol"] for symbol in ("VTI", "AAPL")}
    hurdle = result.outputs["hurdle"]["rate"]    # worked by hand in the end-to-end test above
    rates = result.outputs["betas"]["rates"]
    years = decide["horizon_years"]
    rows = {row["symbol"]: row for row in result.outputs["summary"]["records"]}
    decided = {row["symbol"]: row for row in result.outputs["decide"]["records"]}
    assert set(rows) == {"VTI", "AAPL"}
    worked = {"VTI": _worked(2500.0, 2000.0, rates["VTI"], hurdle),
              "AAPL": _worked(600.0, 450.0, rates["AAPL"], hurdle)}
    vti, aapl = rows["VTI"], rows["AAPL"]
    assert list(vti)[:2] == ["account", "symbol"] and vti["account"] == ACCOUNT
    assert (vti["value"], aapl["value"]) == (2500.0, 600.0)
    assert vti["weight"] == pytest.approx(2500.0 / 3100.0)
    assert vti["horizon_years"] == years
    # expected return a year is the beta rate itself: (hold_value / value) ** (1 / H) - 1
    assert vti["expected_return"] == pytest.approx(rates["VTI"])
    assert aapl["expected_return"] == pytest.approx(rates["AAPL"])
    # wired from the daily bars, never restated: the plain-Python recomputation, exactly
    assert (vti["beta"], aapl["beta"]) == (pytest.approx(betas["VTI"], rel=1e-9),
                                           pytest.approx(betas["AAPL"], rel=1e-9))
    assert (vti["volatility"], aapl["volatility"]) == (pytest.approx(vols["VTI"], rel=1e-9),
                                                       pytest.approx(vols["AAPL"], rel=1e-9))
    assert vti["embedded_gain"] == pytest.approx(500.0 / 2500.0)         # (value - basis) / value
    assert aapl["embedded_gain"] == pytest.approx(150.0 / 600.0)
    assert vti["tax_cost"] == pytest.approx(worked["VTI"]["liq_tax"] / 2500.0)   # 119 / 2500
    assert aapl["tax_cost"] == pytest.approx(worked["AAPL"]["liq_tax"] / 600.0)  # 35.70 / 600
    for symbol, row in rows.items():
        assert row["break_even_return"] == pytest.approx(worked[symbol]["break_even"])
        assert row["opportunity_cost"] == pytest.approx(
            (worked[symbol]["redeploy_net"] / worked[symbol]["hold_net"]) ** (1.0 / years) - 1.0)
        assert row["decision"] == decided[symbol]["decision"]
    # the break-even inverts the comparison decide made: redeploying at it ties the hold path
    assert vti["opportunity_cost"] > 0.0 and vti["decision"] == "exit"
    assert hurdle > vti["break_even_return"], "the hurdle clears it, which is why VTI exits"
    net_rate = vti["break_even_return"] - decide["redeploy_cost"]
    tied = (net_rate - max(net_rate, 0.0) * decide["redeploy_tax_rate"]
            - decide["risk_premium"])
    assert worked["VTI"]["net"] * (1.0 + tied) ** years == pytest.approx(worked["VTI"]["hold_net"])
    # portfolio: weights over 3100; totals' break-even and opportunity cost are exact because
    # every row shares one redeploy rate
    m = result.outputs["summary"]["metrics"]
    weights = {"VTI": 2500.0 / 3100.0, "AAPL": 600.0 / 3100.0}
    assert (m["positions"], m["total_value"]) == (2, 3100.0)
    assert m["expected_return"] == pytest.approx(sum(weights[s] * rates[s] for s in weights))
    assert m["beta"] == pytest.approx(sum(weights[s] * betas[s] for s in weights))
    assert m["hhi"] == pytest.approx(sum(w ** 2 for w in weights.values()))
    assert m["effective_holdings"] == pytest.approx(1.0 / m["hhi"])
    assert (m["top_symbol"], m["top_weight"]) == ("VTI", pytest.approx(weights["VTI"]))
    assert m["tax_cost"] == pytest.approx(
        sum(w["liq_tax"] for w in worked.values()) / 3100.0)             # (119 + 35.70) / 3100
    total = {k: sum(w[k] for w in worked.values()) for k in ("net", "hold_net", "redeploy_net")}
    assert m["opportunity_cost"] == pytest.approx(
        (total["redeploy_net"] / total["hold_net"]) ** (1.0 / years) - 1.0)
    assert m["break_even_return"] == pytest.approx(
        _break_even(total["net"], total["hold_net"])["break_even"])
    assert m["weighted_volatility"] == pytest.approx(sum(weights[s] * vols[s] for s in weights))
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
    _, expected = _expected_rates(_held_betas(root))
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
