"""ReplayEvents: replay outputs -> dskit-eval-v1 events (ADR-0183 item 14)."""

import json
import os

import pytest
from dskit.pipeline.node import DEFAULT_NODE_KINDS, NodeContext


from intraday_equities.evaluation import KIND_ORDER, NODE_KINDS, SCHEMA, ReplayEvents
from intraday_equities.nodes import portfolio_candidates
from intraday_equities.replay import CashFlowPolicy, FillPolicy, ReplayAdapter

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS = os.path.join(CHILD_ROOT, "configs")
T0 = 1_767_623_400_000  # 2026-01-05 14:30 UTC, a Monday session open
MIN = 60_000

PARAMS = {
    "title": "synthetic replay",
    "project": "intraday_equities",
    "tz": "America/New_York",
    "price_field": "close",
    "mark_symbols": "traded",
    "criteria": [{"name": "sharpe", "stat": "sharpe", "op": ">=", "value": 0.0, "min_n": 5}],
    "trials": 3,
    "model": "ridge",
}


def _t(i):
    return T0 + i * MIN


def _bar(symbol, i, px):
    return {"symbol": symbol, "asof_ms": _t(i), "open": px, "close": px + 0.5, "halted": False}


def _scenario(cash=True):
    """Three stamps over two names: an entry, a cash refusal, an unknown bar."""
    bars = [_bar("AAA", i, 10.0) for i in range(6)] + [_bar("BBB", i, 20.0) for i in range(5)]
    book = {
        "per_t": {
            _t(0): {"AAA": 0.03, "BBB": 0.01, "SPY": 0.09},
            _t(1): {"AAA": 0.00, "BBB": 0.02},
            _t(5): {"AAA": 0.01, "BBB": 0.04},
        },
        "tradable": frozenset({"AAA", "BBB"}),
    }
    picks = [
        {"asof_ms": _t(0), "symbol": "AAA"},
        {"asof_ms": _t(1), "symbol": "BBB"},
        {"asof_ms": _t(5), "symbol": "BBB"},  # BBB has no bar at t5
    ]
    decisions = [
        {"symbol": "AAA", "asof_ms": _t(0), "lead": 2, "qty": 10, "side": "buy"},
        {"symbol": "BBB", "asof_ms": _t(1), "lead": 1, "qty": 1000, "side": "buy"},
        {"symbol": "BBB", "asof_ms": _t(5), "lead": 1, "qty": 1, "side": "buy"},
    ]
    policy = FillPolicy.from_path(os.path.join(CONFIGS, "fill-policy.json"))
    cash_policy = (
        CashFlowPolicy.from_path(os.path.join(CONFIGS, "cash-flow-policy.json")) if cash else None
    )
    out = ReplayAdapter(policy, cash_policy).replay(bars, decisions)
    inputs = {
        "candidates": portfolio_candidates(book, picks),
        "picks": picks,
        "fills": out["fills"],
        "refused": out["refused"],
        "skipped": out["skipped"],
        "cash_flows": out["cash_flows"],
        "bars": bars,
    }
    return inputs, out


def _events(inputs, params=None, ctx=None):
    return ReplayEvents("events", dict(PARAMS, **(params or {}))).run(ctx, inputs)["events"]


def test_the_kind_is_registered_as_a_transform():
    assert NODE_KINDS == {"intraday_equities-replay-events": ReplayEvents}
    assert DEFAULT_NODE_KINDS.get("intraday_equities-replay-events")[0] is ReplayEvents
    assert ReplayEvents.role == "transform"
    assert ReplayEvents.outputs == ("events",)


def test_params_are_validated():
    assert ReplayEvents.validate_params(PARAMS) == []
    bad = dict(PARAMS, tz="Mars/Olympus", mark_symbols="some", trials=0, extra=1)
    problems = " ".join(ReplayEvents.validate_params(bad))
    for word in ("tz", "mark_symbols", "trials", "extra"):
        assert word in problems
    assert ReplayEvents("events", PARAMS).validate_inputs({}) != []


def test_envelope_seq_and_kind_order():
    inputs, _ = _scenario()
    events = _events(inputs)
    assert [e["seq"] for e in events] == list(range(len(events)))
    assert all(e["schema"] == SCHEMA and e["known_ms"] == e["ts_ms"] for e in events)
    keys = [(e["ts_ms"], KIND_ORDER.index(e["kind"])) for e in events[1:-1]]
    assert keys == sorted(keys)
    assert events[0]["kind"] == "run_start" and events[-1]["kind"] == "run_end"
    start = events[0]
    assert start["criteria"] == PARAMS["criteria"] and start["trials"] == 3
    assert start["tz"] == "America/New_York"


def test_each_decision_names_candidates_edge_action_and_the_replays_reason():
    inputs, out = _scenario()
    events = _events(inputs)
    stamp = {e["decision_id"]: e for e in events if e["kind"] == "decision"}
    first = stamp[f"d-{_t(0)}"]
    assert first["chosen"] == "AAA" and first["action"] == "enter"
    assert first["reason"] == "top_score" and first["model"] == "ridge"
    assert [c["instrument"] for c in first["candidates"]] == ["SPY", "AAA", "BBB"]
    assert first["candidates"][0] == {
        "instrument": "SPY", "score": 0.09, "rank": 1, "eligible": False,
        "reason": "not_tradable",
    }
    # Edge is against the best other ELIGIBLE name, not the untradable SPY.
    assert first["edge"] == pytest.approx(0.03 - 0.01)
    # BBB x1000 @ 20 cannot be afforded from the $1,020 seed.
    second = stamp[f"d-{_t(1)}"]
    assert (second["action"], second["reason"]) == ("refuse", "insufficient_cash")
    assert any(r["reason"] == "insufficient_cash" for r in out["refused"])
    third = stamp[f"d-{_t(5)}"]
    assert (third["action"], third["reason"]) == ("refuse", "unknown_decision_bar")
    # Every replay refusal/skip is an event, linked to a decision.
    rejections = [e for e in events if e["kind"] in ("refusal", "skip")]
    assert len(rejections) == len(out["refused"]) + len(out["skipped"])
    assert all(e["decision_id"] in stamp for e in rejections)
    assert sorted(e["reason"] for e in rejections) == sorted(
        r["reason"] for r in out["refused"] + out["skipped"]
    )


def test_entries_get_an_order_and_a_fill_and_expiry_exits_are_decisions():
    inputs, out = _scenario()
    events = _events(inputs)
    orders = {e["order_id"]: e for e in events if e["kind"] == "order"}
    fills = [e for e in events if e["kind"] == "fill"]
    assert len(fills) == len(out["fills"]) == 2
    entry = next(f for f in fills if f["tag"] == "entry")
    exit_ = next(f for f in fills if f["tag"] == "exit")
    assert orders[entry["order_id"]]["decision_id"] == f"d-{_t(0)}"
    assert orders[entry["order_id"]]["ref_price"] == 10.5  # decision-bar close
    assert entry["ref_price"] == 10.5 and entry["price"] == 10.0
    assert entry["ts_ms"] == _t(1) and exit_["ts_ms"] == _t(3)
    exit_decision = next(
        e for e in events
        if e["kind"] == "decision" and e["decision_id"] == orders[exit_["order_id"]]["decision_id"]
    )
    assert exit_decision["action"] == "exit"
    assert exit_decision["reason"] == "horizon_expiry"
    assert exit_decision["ts_ms"] == exit_["ts_ms"]


def test_cash_flows_and_marks():
    inputs, out = _scenario()
    events = _events(inputs)
    flows = [e for e in events if e["kind"] == "cashflow"]
    assert [(f["amount"], f["rule"]) for f in flows] == [(1020.0, "initial_capital")]
    marks = [e for e in events if e["kind"] == "mark"]
    # traded: AAA (entered) and BBB (chosen) — every bar close of both.
    assert len(marks) == len(inputs["bars"])
    only_aaa = dict(inputs, picks=[p for p in inputs["picks"] if p["symbol"] == "AAA"])
    marks = [e for e in _events(only_aaa) if e["kind"] == "mark"]
    assert {m["instrument"] for m in marks} == {"AAA"}
    assert all(m["price"] == 10.5 for m in marks)


def test_run_start_reads_the_run_dir_provenance(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"name": "doc"}), encoding="utf-8")
    (tmp_path / "resolved.json").write_text(json.dumps({
        "document_hash": "abc", "run_hash": "r123", "data_fingerprint": {"alpaca": "fp"},
    }), encoding="utf-8")
    inputs, _ = _scenario(cash=False)
    ctx = NodeContext(name="run", asof="2026-01-06", run_dir=str(tmp_path))
    start = _events(inputs, ctx=ctx)[0]
    assert start["run_id"] == "r123" and start["config_hash"] == "abc"
    assert start["config"] == {"name": "doc"} and start["data"] == {"alpaca": "fp"}


def test_events_validate_against_the_schema_when_the_evaluator_is_present():
    events_mod = pytest.importorskip("dskit.evaluation.events")
    inputs, _ = _scenario()
    for obj in _events(inputs):
        events_mod.Event.from_obj(obj)
    events_mod.EventLog(_events(inputs))
