"""Schema v1: every kind validates, unknown fields and bad logs are refused with every problem."""

import copy
import json

import pytest

from dskit.evaluation.events import (
    EVENT_KINDS,
    SCHEMA,
    Decision,
    EvaluationError,
    Event,
    EventLog,
    Fill,
    LocalTime,
    RunStart,
    Solve,
)
from tests.evaluation.conftest import DAY1, Builder, build_scenario


def test_the_scenario_builds_and_covers_every_rendered_kind(scenario):
    log = EventLog(scenario)
    assert len(log) == len(scenario)
    assert {e.kind for e in log} >= set(EVENT_KINDS) - {"solve"}


def test_every_kind_round_trips_through_to_obj(scenario):
    for obj in scenario:
        assert Event.from_obj(obj).to_obj() == obj


def _solve(**fields):
    return {"schema": SCHEMA, "seq": 1, "kind": "solve", "ts_ms": 5, "known_ms": 5,
            "instrument": None, "solver": "highs", "status": "ok", **fields}


def test_solve_is_accepted_with_every_phase_2_field():
    row = {"name": "cap", "rows": 3, "binding": 1, "min_slack": 0.0, "dual": -1.5}
    solve = Event.from_obj(_solve(termination="optimal", model="mio_h02", objective=1.0,
                                  bound=1.0, gap=0.0, seconds=0.01, variables=4,
                                  constraints=3, binding=[row]))
    assert solve.get("binding") == [row]
    assert solve.get("termination") == "optimal" and solve.get("constraints") == 3
    bare = {"name": "cap", "rows": 0, "binding": 0, "min_slack": None}
    assert Event.from_obj(_solve(binding=[bare], objective=None)).get("binding") == [bare]


@pytest.mark.parametrize(("binding", "needle"), [
    (["cap"], "binding[0] must be an object"),
    ([{"name": "cap", "rows": 1}], "missing required field 'binding'"),
    ([{"name": "", "rows": 1, "binding": 0}], "binding[0].name must be a non-empty string"),
    ([{"name": "cap", "rows": -1, "binding": 0}], "binding[0].rows must be an int >= 0"),
    ([{"name": "cap", "rows": 1, "binding": True}], "binding[0].binding must be an int >= 0"),
    ([{"name": "cap", "rows": 1, "binding": 0, "min_slack": "x"}], "min_slack"),
    ([{"name": "cap", "rows": 1, "binding": 0, "dual": float("nan")}], "dual"),
    ([{"name": "cap", "rows": 1, "binding": 0, "shadow": 1}], "unknown field(s) ['shadow']"),
])
def test_solve_binding_rows_are_default_deny(binding, needle):
    with pytest.raises(EvaluationError) as caught:
        Event.from_obj(_solve(binding=binding))
    assert any(needle in p for p in caught.value.problems), caught.value.problems


@pytest.mark.parametrize(("field", "value"), [
    ("termination", 3), ("model", None), ("variables", -1), ("constraints", 1.5),
])
def test_solve_size_and_label_fields_are_typed(field, value):
    with pytest.raises(EvaluationError, match=field):
        Event.from_obj(_solve(**{field: value}))


def test_a_solve_record_is_a_solve_event_body():
    """Pin: the pack's SolveRecord keys are Solve fields, and a real record validates."""
    pytest.importorskip("pyomo")
    from dskit.pipeline.libs.pyomo import BudgetedSelect, SolveRecord
    from dskit.pipeline.node import NodeContext

    names = {field.name for field in Solve.FIELDS}
    assert set(SolveRecord.field_names()) <= names
    node = BudgetedSelect("select", {"budget": 10.0})
    node.run(NodeContext(name="t", asof="2026-01-01", run_dir="unused"),
             {"candidates": [{"id": "A", "cost": 6.0, "value": 9.0},
                             {"id": "B", "cost": 5.0, "value": 6.0}],
              "survivors": ["A", "B"]})
    obj = node.solve_record.to_obj()
    assert Event.from_obj(_solve(**obj, model="select")).get("binding") == obj["binding"]


@pytest.mark.parametrize("kind", sorted(EVENT_KINDS))
def test_an_unknown_field_is_refused_for_every_kind(kind):
    obj = {"schema": SCHEMA, "seq": 0, "kind": kind, "ts_ms": 0, "known_ms": 0,
           "instrument": None, "surprise": 1}
    with pytest.raises(EvaluationError) as caught:
        Event.from_obj(obj)
    assert any("unknown field(s) ['surprise']" in p for p in caught.value.problems)


def test_every_problem_is_listed_at_once():
    obj = {"schema": "v0", "seq": -1, "kind": "fill", "ts_ms": "x", "known_ms": 0,
           "instrument": None, "side": "hold", "qty": 0, "price": float("nan"), "typo": 1}
    with pytest.raises(EvaluationError) as caught:
        Event.from_obj(obj)
    text = "\n".join(caught.value.problems)
    for needle in ("schema", "seq", "ts_ms", "needs an instrument", "typo", "fill_id",
                   "side", "qty", "price"):
        assert needle in text, needle


def test_unknown_kind_and_non_object_are_refused():
    with pytest.raises(EvaluationError, match="kind must be one of"):
        Event.from_obj({"kind": "trade"})
    with pytest.raises(EvaluationError, match="JSON object"):
        Event.from_obj([1, 2])


def test_a_subclass_refuses_another_kind(scenario):
    with pytest.raises(EvaluationError, match="is not a 'fill' event"):
        Fill.from_obj(scenario[0])


def test_decision_look_ahead_is_refused():
    obj = {"schema": SCHEMA, "seq": 1, "kind": "decision", "ts_ms": 100, "known_ms": 101,
           "instrument": None, "decision_id": "d", "action": "hold", "reason": "r"}
    with pytest.raises(EvaluationError, match="look-ahead"):
        Decision(obj)


def test_candidates_are_default_deny_and_chosen_must_be_among_them():
    base = {"schema": SCHEMA, "seq": 1, "kind": "decision", "ts_ms": 1, "known_ms": 1,
            "instrument": None, "decision_id": "d", "action": "enter", "reason": "r"}
    with pytest.raises(EvaluationError, match="unknown field"):
        Event.from_obj({**base, "candidates": [{"instrument": "A", "forecast": 1.0}]})
    with pytest.raises(EvaluationError, match="not among the candidates"):
        Event.from_obj({**base, "candidates": [{"instrument": "A"}], "chosen": "B"})
    decision = Event.from_obj({**base, "chosen": "B", "candidates": [
        {"instrument": "A", "score": 0.1, "rank": 2}, {"instrument": "B", "score": 0.3,
                                                        "rank": 1}]})
    assert decision.chosen_row()["instrument"] == "B"
    assert decision.runner_up()["instrument"] == "A"


def test_a_rejection_must_name_what_it_rejects():
    with pytest.raises(EvaluationError, match="decision_id or an order_id"):
        Event.from_obj({"schema": SCHEMA, "seq": 1, "kind": "refusal", "ts_ms": 1,
                        "known_ms": 1, "instrument": None, "reason": "cap"})


def test_run_start_refuses_a_bad_zone_and_bad_criteria():
    obj = {"schema": SCHEMA, "seq": 0, "kind": "run_start", "ts_ms": 0, "known_ms": 0,
           "instrument": None, "run_id": "r", "tz": "Mars/Olympus",
           "criteria": [{"name": "x", "stat": "net_pnl", "op": "=>", "value": 1}]}
    with pytest.raises(EvaluationError) as caught:
        RunStart(obj)
    text = "\n".join(caught.value.problems)
    assert "IANA" in text and "op must be one of" in text and "missing field(s) ['min_n']" in text


def test_run_start_capture_env_reads_the_runtime_fingerprint():
    env = RunStart.capture_env(packages=["pip"])
    assert set(env) == {"python", "platform", "packages"}
    assert set(env["packages"]) <= {"pip"}


def _mutated(mutate):
    events = copy.deepcopy(build_scenario())
    mutate(events)
    return events


@pytest.mark.parametrize("mutate, needle", [
    (lambda ev: ev[3].update(seq=2), "must exceed the previous seq"),
    (lambda ev: ev[3].update(ts_ms=DAY1 - 1), "goes backwards"),
    (lambda ev: ev.pop(0), "first event must be run_start"),
    (lambda ev: ev.append({**ev[0], "seq": 999, "ts_ms": 10**13, "known_ms": 10**13}),
     "nothing may follow run_end"),
    (lambda ev: ev[5].update(decision_id="nope"), "names no earlier decision"),
    (lambda ev: ev[6].update(order_id="nope"), "names no earlier order"),
    (lambda ev: ev[10].update(fill_id="f1"), "already logged"),
])
def test_log_level_rules_are_refused(mutate, needle):
    with pytest.raises(EvaluationError) as caught:
        EventLog(_mutated(mutate))
    assert any(needle in p for p in caught.value.problems), caught.value.problems


def test_an_outcome_must_come_after_its_decision():
    b = Builder()
    b.add("run_start", 0, run_id="r", tz="UTC")
    b.add("decision", 10, decision_id="d", action="hold", reason="r")
    b.add("outcome", 10, decision_id="d", realized=1.0)
    with pytest.raises(EvaluationError, match="outcome must come after"):
        EventLog(b.events)


def test_all_bad_events_in_a_log_are_reported_together():
    events = _mutated(lambda ev: (ev[2].update(price=-1), ev[4].update(action="buy")))
    with pytest.raises(EvaluationError) as caught:
        EventLog(events)
    text = "\n".join(caught.value.problems)
    assert "event[2].price" in text and "event[4].action" in text


def test_emit_assigns_seq_and_schema():
    log = EventLog()
    log.emit("run_start", 0, 0, run_id="r", tz="UTC")
    mark = log.emit("mark", 5, 5, "AAA", price=10.0)
    assert (mark.seq, mark.to_obj()["schema"]) == (1, SCHEMA)
    assert log.instruments() == ["AAA"]
    assert log.run_end is None


def test_jsonl_is_canonical_and_reread_identically(tmp_path, scenario):
    log = EventLog(scenario)
    path = tmp_path / "events.jsonl"
    log.write(str(path))
    raw = path.read_bytes()
    first = json.loads(raw.splitlines()[0])
    assert raw.splitlines()[0] == json.dumps(first, sort_keys=True, separators=(",", ":")).encode()
    again = EventLog.read(str(path))
    assert again.to_jsonl() == raw
    assert [e.to_obj() for e in again] == scenario


def test_read_names_an_unparseable_line(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text('{"a": 1}\nnot json\n', encoding="utf-8")
    with pytest.raises(EvaluationError, match="bad.jsonl:2: not JSON"):
        EventLog.read(str(path))


def test_local_time_renders_in_the_zone():
    local = LocalTime("America/New_York")
    assert local.stamp(DAY1) == "2026-09-01 10:30:00"
    assert local.day(DAY1) == "2026-09-01"
    assert local.offset_ms(DAY1) == -4 * 3_600_000
