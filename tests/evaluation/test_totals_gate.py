"""The exact-match gate: event totals against expected values, or no report (ADR-0283).

``EventTotalsGate`` recomputes the book's totals from an event list and
compares each to a value the document wires in (never typed into the
node's params), inside a tolerance the document declares. A miss raises,
every miss listed, so the node that renders the report never runs.
"""

import pytest

from dskit.evaluation.events import EvaluationError
from dskit.evaluation.gate import EventTotalsGate, GateRefusal
from dskit.pipeline.document import NodeSpec, OutputsConfig, PipelineDocument
from dskit.pipeline.driver import run_document
from dskit.pipeline.node import Node
from tests.evaluation.conftest import Builder


def _events(pnl_close=130.0):
    """Deposit 1000; buy 1 @ 100 fee 2; sell 1 @ ``pnl_close`` -> net pnl pnl_close - 102."""
    b = Builder()
    b.add("run_start", 0, run_id="r", tz="UTC")
    b.add("cashflow", 1, amount=1000.0)
    b.add("fill", 2, "AAA", fill_id="a", side="buy", qty=1, price=100.0, fee=2.0)
    b.add("fill", 3, "AAA", fill_id="b", side="sell", qty=1, price=pnl_close)
    b.add("run_end", 4, status="ok")
    return b.events


CHECKS = {"net_pnl": {"expected": "pnl", "tolerance": 0.005},
          "final_equity": {"expected": "final_equity", "tolerance": 0.005},
          "contributions": {"expected": "contributions", "tolerance": 0.005}}
EXPECTED = {"pnl": 28.0, "final_equity": 1028.0, "contributions": 1000.0}


def _gate(checks=CHECKS):
    return EventTotalsGate("gate", {"checks": checks})


def test_matching_totals_pass_the_events_through_untouched():
    events = _events()
    out = _gate().run(None, {"events": events, "expected": EXPECTED})
    assert out["events"] is events
    assert [(r["measure"], r["ok"]) for r in out["comparison"]] == [
        ("net_pnl", True), ("final_equity", True), ("contributions", True)]
    assert out["comparison"][0]["actual"] == pytest.approx(28.0)


def test_one_cent_off_refuses_and_names_the_measure():
    with pytest.raises(EvaluationError, match=r"net_pnl.*expected 28\.01.*got 28\.0") as caught:
        _gate().run(None, {"events": _events(), "expected": dict(EXPECTED, pnl=28.01)})
    assert len(caught.value.problems) == 1


def test_every_mismatch_is_listed_at_once():
    expected = {"pnl": 0.0, "final_equity": 0.0, "contributions": 0.0}
    with pytest.raises(EvaluationError) as caught:
        _gate().run(None, {"events": _events(), "expected": expected})
    assert len(caught.value.problems) == 3


def test_the_tolerance_is_the_documents_not_the_codes():
    loose = {"net_pnl": {"expected": "pnl", "tolerance": 0.05}}
    _gate(loose).run(None, {"events": _events(), "expected": dict(EXPECTED, pnl=28.01)})
    strict = {"net_pnl": {"expected": "pnl", "tolerance": 0.0}}
    with pytest.raises(EvaluationError):
        _gate(strict).run(None, {"events": _events(), "expected": dict(EXPECTED, pnl=28.000001)})


def test_a_missing_or_non_numeric_expected_value_refuses():
    with pytest.raises(EvaluationError, match="expected has no 'pnl'"):
        _gate().run(None, {"events": _events(), "expected": {"final_equity": 1.0}})
    problems = _gate().validate_inputs({"events": _events(), "expected": {"pnl": "28"}})
    assert any("expected['pnl'] must be a finite number" in p for p in problems)


def test_params_are_default_deny_and_tolerance_is_required():
    assert EventTotalsGate.validate_params({"checks": CHECKS}) == []
    text = "\n".join(EventTotalsGate.validate_params({"checks": {
        "median": {"expected": "x", "tolerance": 0.1},
        "net_pnl": {"expected": "pnl"},
        "final_equity": {"expected": "", "tolerance": -1, "extra": 1}}, "typo": 1}))
    assert "unknown param" in text and "typo" in text
    assert "median is not a measure" in text
    assert "net_pnl.tolerance is required" in text
    assert "final_equity.expected must be a non-empty string" in text
    assert "final_equity.tolerance must be a finite number >= 0" in text
    assert "final_equity: unknown key(s) ['extra']" in text
    assert "checks is required" in "\n".join(EventTotalsGate.validate_params({}))


class _Source(Node):
    role = "data"
    outputs = ("events", "expected")

    def run(self, ctx, inputs):
        return {"events": _events(), "expected": dict(EXPECTED, pnl=28.01)}


class _Writer(Node):
    role = "report"
    outputs = ("paths",)
    _PARAMS = ("path", "notes")

    def run(self, ctx, inputs):
        with open(self.params["path"], "w") as fh:
            fh.write("rendered")
        return {"paths": {}}


def _document(tmp_path):
    class_path = "tests.evaluation.test_totals_gate"
    return PipelineDocument(
        name="gated",
        pipeline={
            "src": NodeSpec(uses=f"{class_path}:_Source"),
            "gate": NodeSpec(uses="dskit.evaluation.gate:EventTotalsGate",
                             inputs={"events": "$src.events", "expected": "$src.expected"},
                             params={"checks": CHECKS}),
            "write": NodeSpec(uses=f"{class_path}:_Writer",
                              inputs={"events": "$gate.events"},
                              params={"path": str(tmp_path / "out.txt")}),
        },
        outputs=OutputsConfig(run_root=str(tmp_path / "runs")),
    )


def test_a_refusal_in_a_document_stops_the_report_node(tmp_path):
    result = run_document(_document(tmp_path), "2026-10-29")
    assert result.exit_code != 0
    assert not (tmp_path / "out.txt").exists()


def test_the_gate_is_not_cleared_for_a_served_tick():
    assert EventTotalsGate.serving_effect({}, {}) == "forbidden"


def test_a_miss_has_the_gates_own_header_not_the_event_logs():
    with pytest.raises(GateRefusal) as caught:
        _gate().run(None, {"events": _events(), "expected": dict(EXPECTED, pnl=28.01)})
    text = str(caught.value)
    assert text.startswith("event totals gate refused (1 miss):")
    assert "invalid event log" not in text
    assert isinstance(caught.value, EvaluationError) and len(caught.value.problems) == 1


def test_event_counts_are_measures_with_a_zero_tolerance_available():
    """Counts of fills, refusals and decisions are compared like money (ADR-0283)."""
    counts = {"fill_count": {"expected": "fills", "tolerance": 0},
              "refusal_count": {"expected": "refusals", "tolerance": 0},
              "decision_count": {"expected": "decisions", "tolerance": 0}}
    events = _events()
    out = _gate(counts).run(None, {"events": events, "expected": {
        "fills": 2, "refusals": 0, "decisions": 0}})
    assert [r["actual"] for r in out["comparison"]] == [2, 0, 0]
    with pytest.raises(GateRefusal, match=r"fill_count: expected 3, got 2"):
        _gate(counts).run(None, {"events": events, "expected": {
            "fills": 3, "refusals": 0, "decisions": 0}})


def test_the_realised_measure_is_the_books_average_cost_realised_net_of_fees():
    """Buy 1 @ 100 (fee 2), sell 1 @ 130: realised = 30 - 2 = 28, the position closed."""
    check = {"realised": {"expected": "realised", "tolerance": 0.005}}
    out = _gate(check).run(None, {"events": _events(), "expected": {"realised": 28.0}})
    assert out["comparison"][0]["measure"] == "realised"
    assert out["comparison"][0]["actual"] == pytest.approx(28.0) and out["comparison"][0]["ok"]
    with pytest.raises(GateRefusal, match=r"realised: expected 30\.0, got 28\.0"):
        _gate(check).run(None, {"events": _events(), "expected": {"realised": 30.0}})


def test_the_realised_measure_excludes_unrealised_open_pnl():
    """An open long marked up is unrealised: net_pnl moves, realised does not."""
    b = Builder()
    b.add("run_start", 0, run_id="r", tz="UTC")
    b.add("cashflow", 1, amount=1000.0)
    b.add("fill", 2, "AAA", fill_id="a", side="buy", qty=1, price=100.0, fee=2.0)
    b.add("mark", 3, "AAA", price=150.0)
    b.add("run_end", 4, status="ok")
    checks = {"realised": {"expected": "realised", "tolerance": 0.005},
              "net_pnl": {"expected": "pnl", "tolerance": 0.005}}
    out = _gate(checks).run(None, {"events": b.events,
                                   "expected": {"realised": -2.0, "pnl": 48.0}})
    assert [r["actual"] for r in out["comparison"]] == [pytest.approx(-2.0), pytest.approx(48.0)]
