"""The census: reported, never raised; every silently dropped outcome is named."""

from dskit.evaluation.events import EventLog
from tests.evaluation.conftest import Builder


def _log(*extra):
    b = Builder()
    b.add("run_start", 0, run_id="r", tz="UTC")
    for kind, fields in extra:
        b.add(kind, 10 + len(b.events), fields.pop("instrument", None), **fields)
    return EventLog(b.events)


def test_the_scenario_is_fully_accounted_for(scenario):
    census = EventLog(scenario).census()
    body = census.to_obj()
    assert census.ok and body["problems"] == []
    assert body["decisions"] == 8 and body["identity"] is True
    assert body["actions"] == {"enter": 2, "exit": 3, "hold": 1, "skip": 1, "refuse": 1}
    assert (body["orders"], body["fills"], body["refusals"], body["skips"]) == (5, 5, 1, 1)
    assert body["order_states"] == {"filled": 5}
    assert body["unlinked_fills"] == 0


def test_a_refuse_decision_without_a_refusal_is_a_finding_not_an_error():
    census = _log(("decision", {"decision_id": "d1", "action": "refuse", "reason": "cap"})).census()
    assert not census.ok
    assert census.problems == ["1 refuse decision(s) with no refusal event: d1"]


def test_skip_and_enter_without_evidence_are_named():
    census = _log(
        ("decision", {"decision_id": "d1", "action": "skip", "reason": "x"}),
        ("decision", {"decision_id": "d2", "action": "enter", "reason": "x"}),
    ).census()
    assert "1 skip decision(s) with no skip event: d1" in census.problems
    assert "1 enter/exit decision(s) with no order and no rejection: d2" in census.problems


def test_a_refusal_through_the_order_counts_for_the_decision():
    census = _log(
        ("decision", {"decision_id": "d1", "action": "refuse", "reason": "x"}),
        ("order", {"instrument": "A", "order_id": "o1", "decision_id": "d1", "side": "buy",
                   "qty": 1}),
        ("refusal", {"instrument": "A", "order_id": "o1", "reason": "venue"}),
    ).census()
    assert census.ok
    assert census.order_states == {"rejected": 1}


def test_orders_partial_unaccounted_and_overfilled():
    census = _log(
        ("order", {"instrument": "A", "order_id": "o1", "side": "buy", "qty": 10}),
        ("fill", {"instrument": "A", "fill_id": "f1", "order_id": "o1", "side": "buy",
                  "qty": 4, "price": 1.0}),
        ("order", {"instrument": "A", "order_id": "o2", "side": "buy", "qty": 1}),
        ("order", {"instrument": "A", "order_id": "o3", "side": "buy", "qty": 1}),
        ("fill", {"instrument": "A", "fill_id": "f2", "order_id": "o3", "side": "buy",
                  "qty": 2, "price": 1.0}),
        ("fill", {"instrument": "A", "fill_id": "f3", "side": "buy", "qty": 1, "price": 1.0}),
    ).census()
    assert census.order_states == {"partial": 1, "unaccounted": 1, "filled": 1}
    assert census.unlinked_fills == 1
    assert "1 order(s) with no fill and no refusal or skip" in census.problems
    assert "1 order(s) filled beyond their quantity: o3" in census.problems
