"""ADR-0283: a saved MIO run result renders as one exact-match-gated evaluation report.

A synthetic ``result.json`` (two books, one of them two legs; one fill-stage
refusal; a deposit; a decision with a certificate, one with none and a
decision-stage refusal) goes through the real document: ``table-file`` (sha256
pinned) -> ``MioRunRows`` -> ``RowsToEvents`` -> ``EventTotalsGate`` ->
``EvaluationReport``. The shipped document is the same graph; its limited36
node set is checked structurally, never rendered here.

Hand values. Book A: one call leg, 2 contracts filled at 1.50 -> premium
-300.00, fees 1.30, credit -301.30, settles for 500.00, pnl 198.70. Book B: short
3 at 2.00 / long 3 at 1.00 -> premium +300.00, fees 3.90, credit 296.10,
expires worthless (payoff 0), pnl 296.10. Net 494.80; deposits 10000 + 1000
-> final equity 11494.80.
"""

import copy
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import pytest

from dskit.evaluation.book import EvaluationBook
from dskit.evaluation.events import EventLog
from dskit.pipeline.document import NodeSpec, OutputsConfig, PipelineDocument
from dskit.pipeline.driver import run_document
from index_options.mio_run_events import MioRunRows

CHILD = Path(__file__).resolve().parents[1]
SHIPPED = CHILD / "configs/mio-report-limited36-available-size-20261029.json"

METHOD, OTHER = "nominal_lp", "w1_003_lp"
BOOK_A, BOOK_B, BOOK_R = "2019-01-02:AAA:2019-01-18", "2019-01-02:BBB:2019-01-25", "2019-01-02:CCC:2019-02-01"
PNL_A, PNL_B = 198.7, 296.1
DATES = ["2019-01-02", "2019-01-03", "2019-01-16", "2019-01-18", "2019-01-25"]


def _book(book_id, symbol, expiry, legs, contracts, credit, dte):
    return {"id": book_id, "symbol": symbol, "expiry": expiry, "decision_date": "2019-01-02",
            "dte": dte, "contracts": contracts, "credit": credit, "legs": legs}


def _leg(strike, quantity, sign, fill_price, price=None):
    return {"right": "call", "strike": strike, "quantity": quantity, "sign": sign,
            "price": price if price is not None else fill_price, "fill_price": fill_price}


def _trade(book, premium, fees, pnl, fill_date, close_date):
    return {**book, "premium": premium, "fees": fees, "credit": round(premium - fees, 2),
            "pnl": pnl, "fill_date": fill_date, "close_date": close_date,
            "status": "filled"}


def _result(method=METHOD, pnl_shift=0.0, equity_shift=0.0):
    a = _book(BOOK_A, "AAA", "2019-01-18", [_leg(100.0, 2, 1, 1.5)], 2, -301.3, 16)
    b = _book(BOOK_B, "BBB", "2019-01-25", [_leg(100.0, 3, -1, 2.0), _leg(105.0, 3, 1, 1.0)],
              6, 296.1, 23)
    r = _book(BOOK_R, "CCC", "2019-02-01", [_leg(50.0, 1, 1, 0.5)], 1, -51.0, 30)
    trades = [_trade(a, -300.0, 1.3, PNL_A, "2019-01-03", "2019-01-18"),
              _trade(b, 300.0, 3.9, PNL_B, "2019-01-03", "2019-01-25")]
    rejected = [{**r, "fill_date": "2019-01-03", "reason": "missing_fill_quote",
                 "status": "unfilled"}]
    cert = {"solver": "appsi_highs", "status": "ok", "termination": "optimal",
            "objective": 10.0, "bound": 10.5, "gap": 0.05, "seconds": 1.25,
            "variables": 10, "constraints": 4}
    decisions = [
        {"date": "2019-01-02", "status": "selected", "books": [a, b, r], "funding": 10000.0,
         "open_reserve": 0, "candidate_books": 5, "certificates": [cert],
         "refusals": [{"symbol": "DDD", "reason": "no_eligible_forecast_expiry"},
                      {"symbol": "EEE", "expiry": "2019-01-25",
                       "reason": "insufficient_eligible_legs"}]},
        {"date": "2019-01-16", "status": "no_eligible_candidates", "books": [],
         "funding": 12000.0, "open_reserve": 0, "candidate_books": 0, "refusals": []},
    ]
    net = PNL_A + PNL_B
    curve = [{"date": d, "deposit": 1000.0 if d == "2019-01-16" else 0.0,
              "cash": 10000.0, "funding": 10000.0, "reserve": 0, "equity": 10000.0 + k,
              "nav": 1.0, "drawdown": 0.0, "realized": 0.0, "open_ids": [],
              "carried_books": 0, "contributions": 10000.0 + (1000.0 if k >= 2 else 0.0)}
             for k, d in enumerate(DATES)]
    return {"id": method, "replay_parity": True, "status": "completed", "result": {
        "trades": trades, "rejected": rejected, "decisions": decisions,
        "deposits": [{"amount": 1000.0, "date": "2019-01-16"}], "curve": curve,
        "pnl": net + pnl_shift, "final_equity": 11000.0 + net + equity_shift, "contributions": 11000.0,
        "fees": 5.2, "twr": 0.04, "max_drawdown": -0.01, "unfilled": 1,
        "ledger_summary": {"closed": {"expiry": 2}, "orders_opened": 2}}}


def _write(tmp_path, **kw):
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"results": [_result(**kw), _result(method=OTHER)]}))
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


# -- the shaper -------------------------------------------------------------------------

SHAPE = {"method": METHOD, "tz": "America/New_York", "multiplier": 100, "tolerance": 0.005,
         "times": {"decision": "16:00", "fill": "09:30", "expiry": "16:00", "deposit": "08:00"},
         "actions": {"selected": "enter", "cash": "hold", "no_eligible_candidates": "hold"}}


def _rows(**kw):
    table = {"results": [_result(**kw), _result(method=OTHER)]}
    return MioRunRows("rows", SHAPE).run(None, {"table": table})


def test_a_book_is_one_unit_bought_at_its_net_premium_and_sold_at_its_settlement():
    rows = _rows()
    fills = {f["fill_id"]: f for f in rows["fills"]}
    a_open, a_close = fills[f"{BOOK_A}:open"], fills[f"{BOOK_A}:close"]
    assert (a_open["side"], a_open["qty"], a_open["price"], a_open["fee"]) == ("buy", 1, 300.0, 1.3)
    assert (a_close["side"], a_close["price"], a_close["fee"]) == ("sell", pytest.approx(500.0), 0)
    # a credit book is bought at a negative price (the credit); expiring worthless closes at 0
    b_open, b_close = fills[f"{BOOK_B}:open"], fills[f"{BOOK_B}:close"]
    assert b_open["price"] == -300.0 and b_close["price"] == pytest.approx(0.0, abs=1e-9)
    assert a_open["instrument"] == BOOK_A and a_close["asof_ms"] > a_open["asof_ms"]
    assert len(rows["fills"]) == 4


def _local_ms(day, clock):
    """Epoch ms of ``day`` at the SHAPE time ``clock`` slot, computed independently of the node."""
    from datetime import datetime, time
    from zoneinfo import ZoneInfo
    moment = datetime.combine(datetime.fromisoformat(day).date(),
                              time.fromisoformat(SHAPE["times"][clock]),
                              tzinfo=ZoneInfo(SHAPE["tz"]))
    return int(moment.timestamp() * 1000)


def test_each_fill_is_dated_by_its_own_date_at_its_own_time_of_day():
    rows = _rows()
    fills = {f["fill_id"]: f for f in rows["fills"]}
    trades = {t["id"]: t for t in _result()["result"]["trades"]}
    assert len(fills) == 4
    for book_id, trade in trades.items():
        assert fills[f"{book_id}:open"]["asof_ms"] == _local_ms(trade["fill_date"], "fill")
        assert fills[f"{book_id}:close"]["asof_ms"] == _local_ms(trade["close_date"], "expiry")
    # the two clocks and the two dates differ, so a swap of either cannot hide
    assert trades[BOOK_A]["fill_date"] != trades[BOOK_A]["close_date"]
    assert SHAPE["times"]["fill"] != SHAPE["times"]["expiry"]


def test_the_other_ports_carry_orders_refusals_cashflows_decisions_and_expected_values():
    rows = _rows()
    assert [o["order_id"] for o in rows["orders"]] == [BOOK_A, BOOK_B, BOOK_R]
    assert {o["decision_id"] for o in rows["orders"]} == {"d-2019-01-02"}
    (refusal,) = rows["refusals"]
    assert (refusal["order_id"], refusal["reason"]) == (BOOK_R, "missing_fill_quote")
    assert [(c["amount"], c["rule"]) for c in rows["cashflows"]] == [
        (10000.0, "initial_capital"), (1000.0, "scheduled_deposit")]
    assert [(d["decision_id"], d["action"]) for d in rows["decisions"]] == [
        ("d-2019-01-02", "enter"), ("d-2019-01-16", "hold")]
    assert len(rows["decisions"][0]["certificates"]) == 1
    assert rows["decisions"][1]["certificates"] == []
    assert "no_eligible_forecast_expiry 1" in rows["decisions"][0]["detail"]
    assert [(s["instrument"], s["reason"]) for s in rows["skips"]] == [
        ("DDD", "no_eligible_forecast_expiry"), ("EEE", "insufficient_eligible_legs")]
    assert rows["expected"]["pnl"] == pytest.approx(PNL_A + PNL_B)
    assert rows["expected"]["final_equity"] == pytest.approx(11494.8)
    assert rows["expected"]["contributions"] == 11000.0 and rows["expected"]["fees"] == 5.2
    assert {m["figure"] for m in rows["marked"]} >= {"twr", "max_drawdown", "pnl"}
    # counts the gate compares: two fills per opened-and-closed book, the saved unfilled, decisions
    assert (rows["expected"]["fills"], rows["expected"]["refusals"],
            rows["expected"]["decisions"]) == (4, 1, 2)


def test_the_flat_rows_for_the_ticker_dte_tables_drop_the_nested_fields():
    rows = _rows()
    assert [r["symbol"] for r in rows["filled"]] == ["AAA", "BBB"]
    assert all(not isinstance(v, (list, dict)) for r in rows["filled"] + rows["refused"]
               for v in r.values())
    assert rows["refused"][0]["reason"] == "missing_fill_quote" and rows["refused"][0]["dte"] == 30


def test_the_day_by_day_ledger_joins_attempts_fills_refusals_expiries_and_the_marked_curve():
    ledger = {row["date"]: row for row in _rows()["ledger"]}
    assert [r["date"] for r in _rows()["ledger"]] == DATES
    day = ledger["2019-01-03"]
    assert (day["selected_books"], day["selected_contracts"]) == (3, 9)
    assert (day["filled_books"], day["filled_contracts"], day["refused_books"]) == (2, 8, 1)
    assert day["entry_fees"] == pytest.approx(5.2) and day["refusal_reasons"] == "missing_fill_quote 1"
    assert ledger["2019-01-16"]["deposit"] == 1000.0
    expiry = ledger["2019-01-18"]
    assert (expiry["expired_books"], expiry["expiry_pnl"]) == (1, pytest.approx(PNL_A))
    assert ledger["2019-01-25"]["expiry_pnl"] == pytest.approx(PNL_B)
    assert expiry["equity"] == 10003 and "cash" in expiry and "reserve" in expiry


def test_the_saved_premium_must_agree_with_its_legs_and_its_credit():
    bad = _result()
    bad["result"]["trades"][0]["legs"][0]["fill_price"] = 1.51
    with pytest.raises(ValueError, match=r"premium.*legs"):
        MioRunRows("rows", SHAPE).run(None, {"table": {"results": [bad]}})
    bad = _result()
    bad["result"]["trades"][1]["credit"] += 0.02
    with pytest.raises(ValueError, match=r"credit.*premium"):
        MioRunRows("rows", SHAPE).run(None, {"table": {"results": [bad]}})


def _shift_premium(result, delta):
    """Move book A's saved premium by ``delta`` and keep its credit consistent with it."""
    trade = result["result"]["trades"][0]
    trade["premium"] = round(trade["premium"] + delta, 6)
    trade["credit"] = round(trade["premium"] - trade["fees"], 6)


@pytest.mark.parametrize("delta", [0.02, -0.02])
def test_a_premium_off_its_legs_either_way_refuses_alone(delta):
    result = _result()
    _shift_premium(result, delta)
    with pytest.raises(ValueError) as caught:
        MioRunRows("rows", SHAPE).run(None, {"table": {"results": [result]}})
    assert re.search(r"premium.*legs", str(caught.value))
    assert "differs from premium - fees" not in str(caught.value)


@pytest.mark.parametrize("delta", [0.02, -0.02])
def test_a_credit_off_premium_less_fees_either_way_refuses_alone(delta):
    result = _result()
    result["result"]["trades"][1]["credit"] = round(
        result["result"]["trades"][1]["credit"] + delta, 6)
    with pytest.raises(ValueError) as caught:
        MioRunRows("rows", SHAPE).run(None, {"table": {"results": [result]}})
    assert re.search(r"credit.*premium - fees", str(caught.value))
    assert "differs from its legs" not in str(caught.value)


@pytest.mark.parametrize("delta", [0.004, -0.004])
def test_a_premium_or_credit_inside_the_half_cent_passes(delta):
    result = _result()
    _shift_premium(result, delta)
    result["result"]["trades"][1]["credit"] += delta
    assert MioRunRows("rows", SHAPE).run(None, {"table": {"results": [result]}})["fills"]


@pytest.mark.parametrize("edit, needle", [
    (lambda r: r["result"]["decisions"][0].update(status="mystery"), "status 'mystery'"),
    (lambda r: r["result"]["trades"][0].update(decision_date="2019-06-06"), "no decision"),
    (lambda r: r["result"]["trades"].append(copy.deepcopy(r["result"]["trades"][0])), "twice"),
    (lambda r: r["result"]["curve"].pop(1), "outside the curve"),
    # each date the curve must hold, one at a time (the curve is the account's session list)
    (lambda r: r["result"]["trades"][0].update(close_date="2019-01-17"),
     f"book '{BOOK_A}' close_date 2019-01-17 is outside the curve"),
    (lambda r: r["result"]["trades"][0].update(fill_date="2019-01-04"),
     f"book '{BOOK_A}' fill_date 2019-01-04 is outside the curve"),
    (lambda r: r["result"]["rejected"][0].update(fill_date="2019-01-04"),
     f"refused book '{BOOK_R}' fill_date 2019-01-04 is outside the curve"),
    # the order of dates: decision < fill <= close; a refused book's fill follows its decision
    (lambda r: r["result"]["trades"][0].update(fill_date="2019-01-02"), "after its decision"),
    (lambda r: r["result"]["rejected"][0].update(fill_date="2019-01-02"), "after its decision"),
    (lambda r: r["result"]["trades"][0].update(close_date="2019-01-02"),
     "on or before its close"),
    (lambda r: r["result"]["trades"][1].update(fill_date="2019-01-25", close_date="2019-01-18"),
     "on or before its close"),
    # saved counts must agree with the saved rows
    (lambda r: r["result"].update(unfilled=2), "unfilled"),
    (lambda r: r["result"]["ledger_summary"].update(closed={"expiry": 3}), "closed"),
    (lambda r: r["result"]["ledger_summary"].update(orders_opened=3), "orders_opened"),
])
def test_unaccounted_rows_refuse(edit, needle):
    result = _result()
    edit(result)
    with pytest.raises(ValueError, match=needle):
        MioRunRows("rows", SHAPE).run(None, {"table": {"results": [result]}})


def test_the_method_must_be_present_exactly_once():
    with pytest.raises(ValueError, match="no result with id 'nope'"):
        MioRunRows("rows", dict(SHAPE, method="nope")).run(None, {"table": {"results": [_result()]}})
    with pytest.raises(ValueError, match="2 results with id"):
        MioRunRows("rows", SHAPE).run(None, {"table": {"results": [_result(), _result()]}})


def test_params_are_default_deny():
    assert MioRunRows.validate_params(SHAPE) == []
    text = "\n".join(MioRunRows.validate_params(
        {**SHAPE, "typo": 1, "tz": "Mars/Base", "times": {"fill": "9:30pm"}, "tolerance": -1}))
    assert "typo" in text and "tz" in text and "times" in text and "tolerance" in text




# -- the document, end to end on the fixture ---------------------------------------------

BRANCHES = (METHOD, OTHER)


def _document(tmp_path, kinds=BRANCHES, **shift):
    """The shipped document's branches for ``kinds``, re-pointed at a fixture and tmp dirs."""
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"results": [_result(**shift), _result(method=OTHER)]}))
    shipped = json.loads(SHIPPED.read_text())
    pipeline = {}
    for key, spec in shipped["pipeline"].items():
        if not (key == "result" or any(key.startswith(f"{m}_") for m in kinds)):
            continue
        spec, params = copy.deepcopy(spec), copy.deepcopy(spec.get("params", {}))
        if key == "result":
            params.update(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                          expect=1)
        if key.endswith("_report"):
            params["out_dir"] = str(tmp_path / "reports" / key)
        if key.endswith("_stress_links"):
            params["root"] = str(tmp_path / "no-such-stress-root")
        pipeline[key] = NodeSpec(uses=spec["uses"], inputs=spec.get("inputs", {}), params=params)
    return PipelineDocument(name="mio-report-fixture", pipeline=pipeline,
                            outputs=OutputsConfig(run_root=str(tmp_path / "runs")))


def _events(tmp_path, key):
    text = (tmp_path / "reports" / key / "events.jsonl").read_text()
    return [json.loads(line) for line in text.splitlines()]


def test_the_shipped_document_renders_each_method_and_its_events_reproduce_the_saved_totals(
        tmp_path):
    result = run_document(_document(tmp_path), "2019-12-31", journal=False)
    assert result.exit_code == 0, result
    for method in BRANCHES:
        report = tmp_path / "reports" / f"{method}_report"
        for name in ("summary.md", "report.html", "events.jsonl", "decisions.csv", "trades.csv",
                     "ledger.csv", "marked-figures.csv", "gate-comparison.csv",
                     "ticker-dte.csv", "refusals-ticker-dte.csv"):
            assert (report / name).is_file(), (method, name)
        page = (report / "report.html").read_text(encoding="utf-8")
        for anchor in ("equity", "trades", "decisions", "optimizer", "cash", "distributions",
                       "periods", "provenance", "ledger", "ticker-dte"):
            assert f'<section id="{anchor}"' in page, (method, anchor)
        assert "Fill-stage refusals by ticker, DTE and reason" in page
        assert "missing_fill_quote" in page and "no_eligible_forecast_expiry" in page
        # the gate's own table shows event totals equal to the saved ones
        rows = list(csv.DictReader((report / "gate-comparison.csv").open()))
        assert {r["measure"]: r["ok"] for r in rows} == {
            "net_pnl": "True", "final_equity": "True", "contributions": "True", "fees": "True",
            "fill_count": "True", "refusal_count": "True", "decision_count": "True"}
    events = _events(tmp_path, f"{METHOD}_report")
    kinds = Counter(e["kind"] for e in events)
    assert (kinds["fill"], kinds["refusal"], kinds["cashflow"], kinds["solve"]) == (4, 1, 2, 1)
    assert (kinds["skip"], kinds["decision"], kinds["order"]) == (2, 2, 3)
    book = EvaluationBook(EventLog(events))
    assert float(book.points[-1].trading_pnl) == pytest.approx(PNL_A + PNL_B, abs=1e-9)
    assert float(book.points[-1].equity) == pytest.approx(11494.8, abs=1e-9)


def test_the_stress_links_show_pending_while_the_stress_root_does_not_exist(tmp_path):
    result = run_document(_document(tmp_path), "2019-12-31", journal=False)
    assert result.exit_code == 0, result
    report = tmp_path / "reports" / f"{METHOD}_report"
    summary = (report / "summary.md").read_text()
    assert "pending" in summary and "stress_pins" in summary
    assert not (tmp_path / "no-such-stress-root").exists()
    rows = list(csv.DictReader((report / "stress-links.csv").open()))
    assert rows and {r["status"] for r in rows} == {"pending"}
    other = list(csv.DictReader((tmp_path / "reports" / f"{OTHER}_report"
                                 / "stress-links.csv").open()))
    assert other and {r["status"] for r in other} == {"pending"}


@pytest.mark.parametrize("shift", [{"pnl_shift": 0.01}, {"equity_shift": -0.01}])
def test_a_saved_total_one_cent_off_refuses_and_writes_no_report(tmp_path, shift):
    result = run_document(_document(tmp_path, kinds=(METHOD,), **shift), "2019-12-31", journal=False)
    assert result.exit_code != 0
    assert "net_pnl" in str(result) or "final_equity" in str(result)
    assert not (tmp_path / "reports").exists()


def test_a_total_inside_the_half_cent_still_passes(tmp_path):
    result = run_document(_document(tmp_path, kinds=(METHOD,), pnl_shift=0.004), "2019-12-31", journal=False)
    assert result.exit_code == 0, result


def test_a_result_file_that_is_not_the_pinned_one_refuses(tmp_path):
    document = _document(tmp_path, kinds=(METHOD,))
    document.pipeline["result"].params["sha256"] = "0" * 64
    assert run_document(document, "2019-12-31", journal=False).exit_code != 0
    assert not (tmp_path / "reports").exists()


# -- the shipped document's own shape ------------------------------------------------------

def _shipped():
    return json.loads(SHIPPED.read_text())


def test_the_shipped_document_pins_the_verified_result_by_sha256_and_writes_report_only_dirs():
    document = _shipped()
    source = document["pipeline"]["result"]["params"]
    assert source["sha256"] == "df911d4c89ba65ff9b858ce28d3726cf886ceba16ebdb9534c49bd2a81f351ed"
    assert source["path"].endswith("mio-2019-available-size-20261029/limited36/result.json")
    for key, spec in document["pipeline"].items():
        if key.endswith("_report"):
            out = Path(spec["params"]["out_dir"])
            assert out.is_absolute() and "mio-run-report-limited36" in str(out)
            assert not str(out).startswith(source["path"].rsplit("/", 1)[0])


def test_the_four_branches_differ_only_in_their_method():
    pipeline = _shipped()["pipeline"]
    methods = [k[:-len("_rows")] for k in pipeline
               if k.endswith("_rows") and not k.endswith("_solve_rows")]
    assert len(methods) == 4

    def normalised(method):
        text = json.dumps({k[len(method) + 1:]: v for k, v in pipeline.items()
                           if k.startswith(f"{method}_") and not k.endswith("_stress_links")},
                          sort_keys=True)
        return text.replace(method, "M")
    assert len({normalised(m) for m in methods}) == 1
    links = [pipeline[f"{m}_stress_links"] for m in methods]       # one grid, every method's axis
    assert all(link["params"] == links[0]["params"] for link in links)
    assert [link["inputs"] for link in links] == [{"paths": f"${m}_report.paths"} for m in methods]
    assert [pipeline[f"{m}_rows"]["params"]["method"] for m in methods] == methods


def test_the_two_dte_band_blocks_are_identical_and_each_dte_has_exactly_one_band():
    pipeline = _shipped()["pipeline"]
    for method in ("nominal_lp", "w1_003_lp", "w1_003_shock10_lp", "w1_003_component25_lp"):
        filled = pipeline[f"{method}_filled_band"]["params"]
        refused = pipeline[f"{method}_refused_band"]["params"]
        assert filled == refused
        for dte in range(1, 61):
            matches = [case["value"] for case in filled["cases"][:-1]
                       if all(_holds(clause, dte) for clause in case["when"])]
            assert len(matches) == 1, (dte, matches)
        assert filled["cases"][-1] == {"when": [], "value": "other"}


def _holds(clause, dte):
    assert clause["field"] == "dte"
    return {">=": dte >= clause["value"], "<=": dte <= clause["value"]}[clause["op"]]


def test_the_expected_values_come_from_the_pinned_file_not_the_document():
    for key, spec in _shipped()["pipeline"].items():
        if key.endswith("_gate"):
            assert spec["inputs"]["expected"] == f"${key[:-5]}_rows.expected"
            assert all(set(check) == {"expected", "tolerance"}
                       for check in spec["params"]["checks"].values())


def test_a_missing_saved_key_is_listed_not_a_bare_key_error():
    result = _result()
    del result["result"]["unfilled"]
    del result["result"]["trades"][0]["premium"]
    del result["result"]["decisions"][0]["funding"]
    del result["result"]["curve"][0]["cash"]
    with pytest.raises(ValueError) as caught:
        MioRunRows("rows", SHAPE).run(None, {"table": {"results": [result]}})
    text = str(caught.value)
    assert not isinstance(caught.value, KeyError)
    for needle in ("unfilled", "premium", "funding", "cash"):
        assert needle in text
    assert "missing" in text
