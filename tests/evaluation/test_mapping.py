"""RowsToEvents / EventMapping: rows -> dskit-eval-v1 from a JSON map (ADR-0183 phase 3a)."""

import json

import pytest

from dskit.evaluation.events import KIND_ORDER, EvaluationError, EventLog
from dskit.evaluation.mapping import EventMapping, RowMap
from dskit.evaluation.nodes import RowsToEvents
from dskit.pipeline.document import NodeSpec, OutputsConfig, PipelineDocument
from dskit.pipeline.driver import run_document
from dskit.pipeline.node import Node, NodeContext
from tests.evaluation.conftest import DAY1, MIN

RUN_START = {"tz": "America/New_York", "title": "Mapped", "project": "demo",
             "units": {"score": "return", "money": "USD"},
             "criteria": [{"name": "positive", "stat": "net_pnl", "op": ">", "value": 0,
                           "min_n": 1}]}

MAP = {
    "decisions": {"instrument": "pick", "fields": {
        "decision_id": {"template": "d-{asof_ms}"}, "chosen": "pick",
        "action": "action", "reason": "why"}},
    "candidates": {"decision_id": {"template": "d-{asof_ms}"}, "fields": {
        "instrument": "symbol", "score": "score", "rank": "rank"}},
    "orders": {"instrument": "symbol", "fields": {
        "order_id": {"template": "o-{asof_ms}"}, "decision_id": {"template": "d-{asof_ms}"},
        "side": "side", "qty": "qty", "ref_price": "ref"}},
    "fills": {"ts": "filled_ms", "instrument": "symbol", "fields": {
        "fill_id": {"template": "f-{asof_ms}"}, "order_id": {"template": "o-{asof_ms}"},
        "side": "side", "qty": "qty", "price": "price", "fee": "fee",
        "tag": {"const": "entry"}}},
    "marks": {"instrument": "symbol", "fields": {"price": "close"}},
    "cashflows": {"fields": {"amount": "amount", "rule": {"const": "deposit"}}},
    "outcomes": {"ts": "known_at", "instrument": "symbol", "fields": {
        "decision_id": {"template": "d-{asof_ms}"}, "realized": "ret"}},
    "solves": {"fields": {"solver": "solver", "status": "status", "seconds": "secs"}},
}


def _rows():
    t0, t1 = DAY1, DAY1 + MIN
    return {
        "decisions": [{"asof_ms": t0, "pick": "AAA", "action": "enter", "why": "top"}],
        "candidates": [
            {"asof_ms": t0, "symbol": "AAA", "score": 0.002, "rank": 1},
            {"asof_ms": t0, "symbol": "BBB", "score": 0.001, "rank": 2},
        ],
        "orders": [{"asof_ms": t0, "symbol": "AAA", "side": "buy", "qty": 10, "ref": 100.0}],
        "fills": [{"asof_ms": t0, "filled_ms": t1, "symbol": "AAA", "side": "buy", "qty": 10,
                   "price": 100.1, "fee": 1.0}],
        "marks": [{"asof_ms": t0, "symbol": "AAA", "close": 100.0},
                  {"asof_ms": t1 + MIN, "symbol": "AAA", "close": 101.0}],
        "cashflows": [{"asof_ms": t0, "amount": 10000}],
        "outcomes": [{"asof_ms": t0, "known_at": t1 + MIN, "symbol": s, "ret": r}
                     for s, r in (("AAA", 0.01), ("BBB", -0.002))],
        "solves": [{"asof_ms": t0, "solver": "highs", "status": "ok", "secs": 0.2}],
    }


def test_every_kind_maps_into_one_ordered_valid_log():
    events = EventMapping(dict(RUN_START, run_id="r1"), MAP).events(_rows())
    log = EventLog(events)
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "run_start" and kinds[-1] == "run_end"
    assert [e["seq"] for e in events] == list(range(len(events)))
    keys = [(e["ts_ms"], KIND_ORDER.index(e["kind"])) for e in events[1:-1]]
    assert keys == sorted(keys)
    # At t0 the cash flow funds first, then the mark, the solve, the decision, the order.
    assert kinds[1:6] == ["cashflow", "mark", "solve", "decision", "order"]
    decision = next(e for e in events if e["kind"] == "decision")
    assert decision["decision_id"] == f"d-{DAY1}" and decision["instrument"] == "AAA"
    assert [c["instrument"] for c in decision["candidates"]] == ["AAA", "BBB"]
    fill = next(e for e in events if e["kind"] == "fill")
    assert fill["ts_ms"] == DAY1 + MIN and fill["tag"] == "entry"
    assert log.census().ok  # every decision, order and fill accounted for
    assert events[0]["title"] == "Mapped" and events[0]["run_id"] == "r1"


def test_provenance_overrides_the_declared_run_start():
    events = EventMapping(dict(RUN_START, run_id="declared"), MAP).events(
        _rows(), {"run_id": "hash", "config_hash": "abc"})
    assert events[0]["run_id"] == "hash" and events[0]["config_hash"] == "abc"


def test_explode_fans_a_dict_or_list_field_out():
    books = {"model": {"pnl": 5.0}, "always": {"pnl": -2.0}}
    rowmap = RowMap("cashflow", {"explode": "books", "fields": {
        "amount": "pnl", "rule": "key"}})
    items = rowmap.items([{"asof_ms": DAY1, "books": books}], "ledger")
    assert [i[1] for i in items] == [{"amount": 5.0, "rule": "model"},
                                     {"amount": -2.0, "rule": "always"}]
    listed = RowMap("mark", {"explode": "legs", "instrument": "symbol",
                             "fields": {"price": "px"}})
    got = listed.items([{"asof_ms": DAY1, "legs": [{"symbol": "A", "px": 1.0},
                                                   {"symbol": "B", "px": 2.0}]}], "p")
    assert [(i[3], i[1]["price"]) for i in got] == [("A", 1.0), ("B", 2.0)]
    with pytest.raises(EvaluationError, match="must be a list or an object to explode"):
        listed.items([{"asof_ms": DAY1, "legs": 3}], "p")


def test_plan_time_problems_are_all_listed():
    bad = {
        "fills": {"instrument": "symbol", "fields": {"fill_id": "id", "side": "side",
                                                     "qty": "qty", "pnl": "pnl"}},
        "marks": {"fields": {"price": {"lookup": "x"}}},
        "bogus": {"fields": {}},
        "candidates": {"fields": {"score": "s"}},
    }
    problems = EventMapping.problems({"tz": "Mars/Base"}, bad)
    text = "\n".join(problems)
    assert "['pnl'] are not fill fields" in text
    assert "map.fills must map ['price'] (required for fill)" in text
    assert "map.marks must map ['instrument'] (required for mark)" in text
    assert "map.marks.fields.price must be a row field name" in text
    assert "unknown port(s) ['bogus']" in text
    assert "map.candidates must map ['instrument', 'decision_id']" in text
    assert "map.candidates needs map.decisions" in text
    assert "run_start.tz" in text
    assert EventMapping.problems(RUN_START, MAP) == []


def test_run_time_failures_are_loud():
    rows = _rows()
    rows["fills"][0].pop("price")
    with pytest.raises(EvaluationError, match=r"fills\[0\] \(price\) has no field 'price'"):
        EventMapping(dict(RUN_START, run_id="r"), MAP).events(rows)
    rows = _rows()
    rows["candidates"].append({"asof_ms": 1, "symbol": "C", "score": 0.0, "rank": 3})
    with pytest.raises(EvaluationError, match="candidates name decision"):
        EventMapping(dict(RUN_START, run_id="r"), MAP).events(rows)
    rows = _rows()
    rows["marks"][0]["asof_ms"] = "noon"
    with pytest.raises(EvaluationError, match=r"must be an int epoch-ms instant"):
        EventMapping(dict(RUN_START, run_id="r"), MAP).events(rows)
    with pytest.raises(EvaluationError, match="run_id"):
        EventMapping(RUN_START, MAP).events(_rows())  # no run_id declared or recorded


def test_the_node_validates_its_ports():
    node = RowsToEvents("events", {"run_start": RUN_START, "map": {"marks": MAP["marks"]}})
    assert node.validate_inputs({"marks": []}) == []
    problems = node.validate_inputs({"fills": [], "marks": 3, "nope": []})
    assert any("fills is wired but map declares no 'fills'" in p for p in problems)
    assert any("marks must be a list" in p for p in problems)
    assert any("unknown input port 'nope'" in p for p in problems)
    assert RowsToEvents.validate_params({"map": {}}) == ["run_start is required"]


class _Rows(Node):
    """A data-role source emitting the synthetic rows, one port each."""

    role = "data"
    outputs = tuple(_rows())

    def run(self, ctx, inputs):
        return _rows()


def test_a_document_plugs_the_evaluator_in_from_json_alone(tmp_path):
    ports = {port: f"$rows.{port}" for port in _rows()}
    document = PipelineDocument(
        name="mapped-report",
        pipeline={
            "rows": NodeSpec(uses="tests.evaluation.test_mapping:_Rows"),
            "events": NodeSpec(uses="dskit.evaluation.nodes:RowsToEvents", inputs=ports,
                               params={"run_start": RUN_START, "map": MAP}),
            "report": NodeSpec(uses="dskit.evaluation.nodes:EvaluationReport",
                               inputs={"events": "$events.events"},
                               params={"out_dir": "report",
                                       "sections": ["decisions", "inference"]}),
        },
        outputs=OutputsConfig(run_root=str(tmp_path)),
    )
    result = run_document(document, "2026-09-24")
    assert result.exit_code == 0, result
    report_dir = tmp_path.glob("mapped-report-*/report")
    report_dir = next(iter(report_dir))
    events = [json.loads(line) for line in (report_dir / "events.jsonl").read_text().splitlines()]
    # The run directory named the run: its hash, not the declared title.
    assert events[0]["run_id"] != "Mapped" and events[0]["config_hash"]
    page = (report_dir / "report.html").read_text(encoding="utf-8")
    assert 'id="decisions"' in page and 'id="inference"' in page and 'id="equity"' not in page


def test_node_run_uses_the_run_dir_when_there_is_one(tmp_path):
    (tmp_path / "resolved.json").write_text(json.dumps({"run_hash": "h1",
                                                         "document_hash": "d1"}))
    node = RowsToEvents("events", {"run_start": RUN_START, "map": MAP})
    ctx = NodeContext(name="t", asof="2026-09-24", run_dir=str(tmp_path))
    events = node.run(ctx, _rows())["events"]
    assert events[0]["run_id"] == "h1" and events[0]["config_hash"] == "d1"
