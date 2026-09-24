"""Gate 5 replay policies — synthetic only (ADR-0120 accepted).

No market data, no sealed P16 artifact load, no real confirmed caps.
Every fill-model value is read from config; the tests below fail if the
adapter invents a default the document did not name.
"""

from __future__ import annotations

import ast
import copy
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from dskit.pipeline.base import config_hash
from dskit.pipeline.document import load_document
from dskit.pipeline.node import ConfigError

from intraday_equities.nodes_capital import SchwabCostModel
from intraday_equities.replay import (
    BarTape,
    CashFlowPolicy,
    DevelopmentReplay,
    EquityReplay,
    FillPolicy,
    ReplayAdapter,
    ScheduledCashFlowPolicy,
)

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS = os.path.join(CHILD_ROOT, "configs")
FILL_POLICY_PATH = os.path.join(CONFIGS, "fill-policy.json")
CASH_FLOW_POLICY_PATH = os.path.join(CONFIGS, "cash-flow-policy.json")
REPLAY_PY = os.path.join(
    CHILD_ROOT, "intraday_equities", "replay.py"
)


def _raw_fill_policy():
    with open(FILL_POLICY_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _policy(overrides=None):
    payload = _raw_fill_policy()
    if overrides:
        payload.update(overrides)
    return FillPolicy(payload)


def _bar(symbol, asof_ms, open_px, close_px, halted=False):
    return {
        "symbol": symbol,
        "asof_ms": asof_ms,
        "open": open_px,
        "close": close_px,
        "halted": halted,
    }


def _decision(symbol, asof_ms, lead, qty=10, side="buy"):
    return {
        "symbol": symbol,
        "asof_ms": asof_ms,
        "lead": lead,
        "qty": qty,
        "side": side,
    }


class _HashView:
    """Hand a mapping to ``config_hash``, which requires ``to_obj``."""

    def __init__(self, obj):
        self._obj = obj

    def to_obj(self):
        return self._obj


def test_shipped_fill_policy_names_every_fill_model_field_and_hashes():
    raw = _raw_fill_policy()
    policy = FillPolicy.from_path(FILL_POLICY_PATH)
    assert policy.fill_bar_offset == raw["fill_bar_offset"]
    assert policy.fill_price_field == raw["fill_price_field"]
    assert policy.decision_price_field == raw["decision_price_field"]
    assert policy.order_type == raw["order_type"]
    assert policy.partial_fills is raw["partial_fills"]
    assert policy.rejections == raw["rejections"]
    assert policy.halt_handling == raw["halt_handling"]
    assert policy.forced_exit_at == raw["forced_exit_at"]
    assert policy.forced_exit_horizon_basis == raw["forced_exit_horizon_basis"]
    assert policy.same_lead_overlap == raw["same_lead_overlap"]
    assert policy.different_lead_overlap == raw["different_lead_overlap"]
    assert policy.same_tick_order == raw["same_tick_order"]
    assert policy.fill_suffix_bars == raw["fill_suffix_bars"]
    assert policy.fill_suffix_weekdays == raw["fill_suffix_weekdays"]
    assert policy.digest() == config_hash(_HashView(raw), exclude=())


def test_fill_policy_refuses_a_missing_or_unknown_knob():
    raw = _raw_fill_policy()
    missing = dict(raw)
    del missing["fill_bar_offset"]
    with pytest.raises(ConfigError, match="fill_bar_offset"):
        FillPolicy(missing)
    with pytest.raises(ConfigError, match="notional_slip"):
        FillPolicy(dict(raw, notional_slip=1))


def test_fill_policy_refuses_a_value_outside_the_closed_vocabulary():
    with pytest.raises(ConfigError, match="partial_fills"):
        _policy({"partial_fills": True})
    with pytest.raises(ConfigError, match="halt_handling"):
        _policy({"halt_handling": "cancel"})
    with pytest.raises(ConfigError, match="same_lead_overlap"):
        _policy({"same_lead_overlap": "stack"})


def test_next_bar_open_fill_uses_only_the_config_offset_and_price_field():
    policy = _policy()
    bars = [
        _bar("AAA", 1_000, 10.0, 10.5),
        _bar("AAA", 2_000, 11.0, 11.5),
        _bar("AAA", 3_000, 12.0, 12.5),
    ]
    out = ReplayAdapter(policy).replay(bars, [_decision("AAA", 1_000, lead=1)])
    entries = [row for row in out["fills"] if row["kind"] == "entry"]
    assert len(entries) == 1
    fill_px = bars[policy.fill_bar_offset][policy.fill_price_field]
    assert entries[0]["price"] == pytest.approx(fill_px)
    assert entries[0]["asof_ms"] == bars[policy.fill_bar_offset]["asof_ms"]
    assert entries[0]["symbol"] == "AAA"


def test_fill_price_field_close_fills_at_close_not_open():
    policy = _policy({"fill_price_field": "close"})
    bars = [
        _bar("AAA", 1_000, 10.0, 10.5),
        _bar("AAA", 2_000, 11.0, 11.5),
        _bar("AAA", 3_000, 12.0, 12.5),
    ]
    out = ReplayAdapter(policy).replay(bars, [_decision("AAA", 1_000, lead=1)])
    entry = next(row for row in out["fills"] if row["kind"] == "entry")
    assert entry["price"] == pytest.approx(11.5)
    assert entry["price"] != pytest.approx(11.0)


def test_a_halted_symbol_is_skipped_not_queued():
    policy = _policy()
    bars = [
        _bar("AAA", 1_000, 10.0, 10.5),
        _bar("AAA", 2_000, 11.0, 11.5, halted=True),
        _bar("AAA", 3_000, 12.0, 12.5),
    ]
    out = ReplayAdapter(policy).replay(bars, [_decision("AAA", 1_000, lead=1)])
    assert out["fills"] == []
    assert out["skipped"] == [
        {"symbol": "AAA", "asof_ms": 2_000, "reason": "halted", "decision_ms": 1_000}
    ]


def test_a_halted_expiry_bar_skips_the_forced_exit():
    policy = _policy()
    bars = [
        _bar("AAA", 1_000, 10.0, 10.5),
        _bar("AAA", 2_000, 11.0, 11.5),
        _bar("AAA", 3_000, 12.0, 12.5, halted=True),
        _bar("AAA", 4_000, 13.0, 13.5),
    ]
    out = ReplayAdapter(policy).replay(bars, [_decision("AAA", 1_000, lead=1)])
    assert not any(row["kind"] == "exit" and row["asof_ms"] == 3_000 for row in out["fills"])
    assert any(row["reason"] == "halted" and row["asof_ms"] == 3_000 for row in out["skipped"])
    delayed = [row for row in out["fills"] if row["kind"] == "exit"]
    assert len(delayed) == 1
    assert delayed[0]["asof_ms"] == 4_000
    assert delayed[0]["price"] == pytest.approx(13.0)


def test_integer_halt_flag_is_refused_not_coerced():
    policy = _policy()
    bars = [
        _bar("AAA", 1_000, 10.0, 10.5),
        {"symbol": "AAA", "asof_ms": 2_000, "open": 11.0, "close": 11.5, "halted": 1},
        _bar("AAA", 3_000, 12.0, 12.5),
    ]
    with pytest.raises(ConfigError, match="halt"):
        ReplayAdapter(policy).replay(bars, [_decision("AAA", 1_000, lead=1)])


def test_unknown_symbol_and_off_tape_fill_are_refused_not_silent():
    policy = _policy()
    orphan = ReplayAdapter(policy).replay(
        [_bar("AAA", 1_000, 10.0, 10.5), _bar("AAA", 2_000, 11.0, 11.5)],
        [_decision("BBB", 1_000, lead=1)],
    )
    assert orphan["fills"] == []
    assert any(row["reason"] == "unknown_symbol" for row in orphan["refused"])
    off_tape = ReplayAdapter(policy).replay(
        [_bar("AAA", 1_000, 10.0, 10.5)],
        [_decision("AAA", 1_000, lead=1)],
    )
    assert off_tape["fills"] == []
    assert any(row["reason"] == "fill_bar_past_tape" for row in off_tape["refused"])


def test_unclosed_lot_at_end_of_tape_is_refused():
    policy = _policy()
    out = ReplayAdapter(policy).replay(
        [_bar("AAA", 1_000, 10.0, 10.5), _bar("AAA", 2_000, 11.0, 11.5)],
        [_decision("AAA", 1_000, lead=1)],
    )
    assert [row["kind"] for row in out["fills"]] == ["entry"]
    assert any(row["reason"] == "expiry_past_tape" for row in out["refused"])


def test_duplicate_asof_on_one_symbol_is_refused():
    policy = _policy()
    bars = [
        _bar("AAA", 1_000, 10.0, 10.5),
        _bar("AAA", 1_000, 11.0, 11.5),
        _bar("AAA", 2_000, 12.0, 12.5),
    ]
    with pytest.raises(ConfigError, match="asof"):
        ReplayAdapter(policy).replay(bars, [_decision("AAA", 1_000, lead=1)])


def test_lead_below_one_is_refused():
    policy = _policy()
    bars = [
        _bar("AAA", 1_000, 10.0, 10.5),
        _bar("AAA", 2_000, 11.0, 11.5),
        _bar("AAA", 3_000, 12.0, 12.5),
    ]
    out = ReplayAdapter(policy).replay(bars, [_decision("AAA", 1_000, lead=0)])
    assert out["fills"] == []
    assert any(row["reason"] == "lead" for row in out["refused"])


def test_forced_exit_is_fill_bar_plus_lead_at_that_bar_open():
    policy = _policy()
    bars = [
        _bar("AAA", 1_000, 10.0, 10.5),
        _bar("AAA", 2_000, 11.0, 11.5),
        _bar("AAA", 3_000, 12.0, 12.5),
        _bar("AAA", 4_000, 13.0, 13.5),
    ]
    out = ReplayAdapter(policy).replay(bars, [_decision("AAA", 1_000, lead=2)])
    kinds = [(row["kind"], row["asof_ms"], row["price"]) for row in out["fills"]]
    assert kinds == [
        ("entry", 2_000, 11.0),
        ("exit", 4_000, 13.0),
    ]


def test_different_leads_on_the_same_name_may_be_open_concurrently():
    policy = _policy()
    bars = [
        _bar("AAA", 1_000, 10.0, 10.5),
        _bar("AAA", 2_000, 11.0, 11.5),
        _bar("AAA", 3_000, 12.0, 12.5),
        _bar("AAA", 4_000, 13.0, 13.5),
        _bar("AAA", 5_000, 14.0, 14.5),
    ]
    out = ReplayAdapter(policy).replay(
        bars,
        [
            _decision("AAA", 1_000, lead=1),
            _decision("AAA", 1_000, lead=2),
        ],
    )
    open_after_entry = [
        row for row in out["fills"] if row["kind"] == "entry"
    ]
    assert {(row["lead"], row["asof_ms"]) for row in open_after_entry} == {
        (1, 2_000),
        (2, 2_000),
    }
    exits = [row for row in out["fills"] if row["kind"] == "exit"]
    assert {(row["lead"], row["asof_ms"]) for row in exits} == {
        (1, 3_000),
        (2, 4_000),
    }


def test_a_second_decision_for_an_open_same_lead_is_refused():
    policy = _policy()
    bars = [
        _bar("AAA", 1_000, 10.0, 10.5),
        _bar("AAA", 2_000, 11.0, 11.5),
        _bar("AAA", 3_000, 12.0, 12.5),
        _bar("AAA", 4_000, 13.0, 13.5),
        _bar("AAA", 5_000, 14.0, 14.5),
    ]
    out = ReplayAdapter(policy).replay(
        bars,
        [
            _decision("AAA", 1_000, lead=2, qty=10),
            _decision("AAA", 2_000, lead=2, qty=7),
        ],
    )
    entries = [row for row in out["fills"] if row["kind"] == "entry"]
    assert len(entries) == 1
    assert entries[0]["qty"] == 10
    assert any(row["reason"] == "same_lead_open" for row in out["refused"])


def test_same_tick_processes_forced_exits_before_new_entries():
    policy = _policy()
    bars = [
        _bar("AAA", 1_000, 10.0, 10.5),
        _bar("AAA", 2_000, 11.0, 11.5),
        _bar("AAA", 3_000, 12.0, 12.5),
        _bar("AAA", 4_000, 13.0, 13.5),
        _bar("AAA", 5_000, 14.0, 14.5),
    ]
    # h1 opened at t=1000 fills at 2000, expires at 3000. A new h1 at
    # t=2000 fills at 3000 — after the expiry — so it is allowed.
    out = ReplayAdapter(policy).replay(
        bars,
        [
            _decision("AAA", 1_000, lead=1, qty=10),
            _decision("AAA", 2_000, lead=1, qty=3),
        ],
    )
    entries = [row for row in out["fills"] if row["kind"] == "entry"]
    assert [(row["qty"], row["asof_ms"]) for row in entries] == [
        (10, 2_000),
        (3, 3_000),
    ]
    assert out["refused"] == []


def test_schwab_fees_are_the_cost_model_applied_to_the_fill_price():
    policy = _policy()
    costs = SchwabCostModel({
        "spread_bps": policy.spread_bps,
        "taf_per_share": policy.taf_per_share,
        "sec31_bps": policy.sec31_bps,
        "min_price": policy.min_price,
    })
    bars = [
        _bar("AAA", 1_000, 10.0, 10.5),
        _bar("AAA", 2_000, 11.0, 11.5),
        _bar("AAA", 3_000, 12.0, 12.5),
    ]
    out = ReplayAdapter(policy).replay(
        bars, [_decision("AAA", 1_000, lead=1, qty=10)]
    )
    entry = next(row for row in out["fills"] if row["kind"] == "entry")
    exit_row = next(row for row in out["fills"] if row["kind"] == "exit")
    assert entry["fee"] == pytest.approx(costs.buy_per_share(11.0) * 10)
    assert exit_row["fee"] == pytest.approx(costs.sell_per_share(12.0) * 10)


def test_a_temp_fill_policy_copy_changes_the_fill_without_editing_python():
    raw = _raw_fill_policy()
    raw["fill_bar_offset"] = 2
    policy = FillPolicy(raw)
    bars = [
        _bar("AAA", 1_000, 10.0, 10.5),
        _bar("AAA", 2_000, 11.0, 11.5),
        _bar("AAA", 3_000, 12.0, 12.5),
        _bar("AAA", 4_000, 13.0, 13.5),
    ]
    out = ReplayAdapter(policy).replay(bars, [_decision("AAA", 1_000, lead=1)])
    entry = next(row for row in out["fills"] if row["kind"] == "entry")
    assert entry["price"] == pytest.approx(12.0)
    assert entry["asof_ms"] == 3_000


def test_replay_py_does_not_hardcode_fill_model_values():
    with open(REPLAY_PY, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    banned_defaults = {
        "fill_bar_offset",
        "fill_price_field",
        "decision_price_field",
        "partial_fills",
        "rejections",
        "halt_handling",
        "forced_exit_at",
        "forced_exit_horizon_basis",
        "same_lead_overlap",
        "different_lead_overlap",
        "same_tick_order",
        "spread_bps",
        "taf_per_share",
        "sec31_bps",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "get":
            if node.args and isinstance(node.args[0], ast.Constant):
                assert node.args[0].value not in banned_defaults
                if len(node.args) > 1:
                    pytest.fail(
                        f"params.get({node.args[0].value!r}, default) in replay.py"
                    )


def test_development_replay_refuses_deployment_eligible_true():
    raw = _raw_fill_policy()
    with pytest.raises(ConfigError, match="deployment_eligible"):
        DevelopmentReplay("replay", {
            "deployment_eligible": True,
            "evidence_end": "2025-10-16",
            "fill_policy": "configs/fill-policy.json",
            "fill_policy_sha256": FillPolicy(raw).digest(),
            "caps": "development-only",
        })


def test_development_replay_bounds_decisions_and_fill_only_trailing_bars():
    raw = _raw_fill_policy()
    node = DevelopmentReplay("replay", {
        "deployment_eligible": False,
        "evidence_end": "2025-10-16",
        "fill_policy": "configs/fill-policy.json",
        "fill_policy_sha256": FillPolicy(raw).digest(),
        "caps": "development-only",
    })
    last_day = 1_760_572_800_000  # 2025-10-16T00:00:00Z
    fill_only = 1_760_659_200_000  # 2025-10-17T00:00:00Z exclusive end
    out = node.run(None, {
        "bars": [
            _bar("AAA", last_day, 10.0, 10.5),
            _bar("AAA", fill_only, 11.0, 11.5),
            _bar("AAA", fill_only + 60_000, 12.0, 12.5),
        ],
        "decisions": [_decision("AAA", last_day, lead=1)],
    })
    entries = [row for row in out["fills"] if row["kind"] == "entry"]
    assert entries[0]["asof_ms"] == fill_only
    with pytest.raises(ConfigError, match="evidence_end"):
        node.run(None, {
            "bars": [_bar("AAA", last_day, 10.0, 10.5)],
            "decisions": [_decision("AAA", fill_only, lead=1)],
        })
    y2026 = 1_767_225_600_000  # 2026-01-01T00:00:00Z
    with pytest.raises(ConfigError, match="evidence_end"):
        node.run(None, {
            "bars": [
                _bar("AAA", last_day, 10.0, 10.5),
                _bar("AAA", y2026, 99.0, 99.5),
                _bar("AAA", y2026 + 60_000, 100.0, 100.5),
            ],
            "decisions": [_decision("AAA", last_day, lead=1)],
        })


def test_run_development_replay_document_is_ineligible_and_pins_the_fill_policy():
    document = load_document(os.path.join(CONFIGS, "run-development-replay.json"))
    assert document.hash
    params = document.pipeline["replay"].params
    assert params["deployment_eligible"] is False
    assert params["evidence_end"] == "2025-10-16"
    assert params["caps"] == "development-only"
    assert params["fill_policy_sha256"] == FillPolicy.from_path(
        os.path.join(CHILD_ROOT, params["fill_policy"])
    ).digest()


def test_two_synthetic_replays_of_the_same_tape_are_byte_identical():
    policy = _policy()
    bars = [
        _bar("AAA", 1_000, 10.0, 10.5),
        _bar("AAA", 2_000, 11.0, 11.5),
        _bar("AAA", 3_000, 12.0, 12.5),
        _bar("BBB", 1_000, 20.0, 20.5),
        _bar("BBB", 2_000, 21.0, 21.5),
        _bar("BBB", 3_000, 22.0, 22.5),
    ]
    decisions = [
        _decision("AAA", 1_000, lead=1),
        _decision("BBB", 1_000, lead=2, qty=4),
    ]

    def run():
        return copy.deepcopy(ReplayAdapter(policy).replay(bars, decisions))

    assert run() == run()


def _call_names(tree):
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def test_replay_py_composes_serveloop_replayfeed_tape_and_shared_clock():
    with open(REPLAY_PY, encoding="utf-8") as fh:
        walk_src = fh.read()
    tree = ast.parse(walk_src)
    calls = _call_names(tree)
    assert "ServeLoop" in calls
    assert "bundles_for" in calls
    assert "OnboardingRoot" in calls or "create" in calls
    tape_bases = [
        base.id
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        for base in node.bases
        if isinstance(base, ast.Name)
    ]
    assert "ReplayTape" in tape_bases
    assert "Cadence" in tape_bases
    assert "tape=tape" in walk_src.replace(" ", "")
    assert "class _Ready" not in walk_src
    assert "class _Accounting" not in walk_src
    assert "for index, bar in enumerate(seq)" not in walk_src
    assert 'digest = "a" * 64' not in walk_src
    assert "account=None" not in walk_src.replace(" ", "")


def test_numpy_bool_halt_flags_are_accepted():
    np = pytest.importorskip("numpy")
    policy = _policy()
    bars = [
        _bar("AAA", 1_000, 10.0, 10.5),
        {
            "symbol": "AAA",
            "asof_ms": 2_000,
            "open": 11.0,
            "close": 11.5,
            "halted": np.True_,
        },
        _bar("AAA", 3_000, 12.0, 12.5),
    ]
    out = ReplayAdapter(policy).replay(bars, [_decision("AAA", 1_000, lead=1)])
    assert out["fills"] == []
    assert any(row["reason"] == "halted" and row["asof_ms"] == 2_000 for row in out["skipped"])
    bars[1]["halted"] = np.False_
    filled = ReplayAdapter(policy).replay(bars, [_decision("AAA", 1_000, lead=1)])
    assert any(row["kind"] == "entry" and row["asof_ms"] == 2_000 for row in filled["fills"])


def test_same_lead_override_from_json_replaces_the_open_lot():
    policy = _policy({"same_lead_overlap": "override"})
    bars = [
        _bar("AAA", 1_000, 10.0, 10.5),
        _bar("AAA", 2_000, 11.0, 11.5),
        _bar("AAA", 3_000, 12.0, 12.5),
        _bar("AAA", 4_000, 13.0, 13.5),
        _bar("AAA", 5_000, 14.0, 14.5),
    ]
    out = ReplayAdapter(policy).replay(
        bars,
        [
            _decision("AAA", 1_000, lead=2, qty=10),
            _decision("AAA", 2_000, lead=2, qty=7),
        ],
    )
    entries = [row for row in out["fills"] if row["kind"] == "entry"]
    assert [(row["qty"], row["asof_ms"]) for row in entries] == [
        (10, 2_000),
        (7, 3_000),
    ]
    exits = [row for row in out["fills"] if row["kind"] == "exit"]
    assert [(row["qty"], row["asof_ms"]) for row in exits] == [
        (10, 3_000),
        (7, 5_000),
    ]
    assert not any(row["reason"] == "same_lead_open" for row in out["refused"])


def test_halt_queue_retries_the_entry_on_the_next_live_bar():
    policy = _policy({"halt_handling": "queue"})
    bars = [
        _bar("AAA", 1_000, 10.0, 10.5),
        _bar("AAA", 2_000, 11.0, 11.5, halted=True),
        _bar("AAA", 3_000, 12.0, 12.5),
        _bar("AAA", 4_000, 13.0, 13.5),
    ]
    out = ReplayAdapter(policy).replay(bars, [_decision("AAA", 1_000, lead=1)])
    entries = [row for row in out["fills"] if row["kind"] == "entry"]
    assert len(entries) == 1
    assert entries[0]["asof_ms"] == 3_000
    assert entries[0]["price"] == pytest.approx(12.0)


def test_halt_queue_past_the_tape_is_refused():
    policy = _policy({"halt_handling": "queue"})
    bars = [
        _bar("AAA", 1_000, 10.0, 10.5),
        _bar("AAA", 2_000, 11.0, 11.5, halted=True),
    ]
    out = ReplayAdapter(policy).replay(bars, [_decision("AAA", 1_000, lead=1)])
    assert out["fills"] == []
    assert any(row["reason"] == "fill_bar_past_tape" for row in out["refused"])


def test_forced_exit_price_field_close_exits_at_close():
    policy = _policy({"forced_exit_price_field": "close"})
    bars = [
        _bar("AAA", 1_000, 10.0, 10.5),
        _bar("AAA", 2_000, 11.0, 11.5),
        _bar("AAA", 3_000, 12.0, 12.5),
    ]
    out = ReplayAdapter(policy).replay(bars, [_decision("AAA", 1_000, lead=1)])
    exit_row = next(row for row in out["fills"] if row["kind"] == "exit")
    assert exit_row["price"] == pytest.approx(12.5)


def test_missing_fill_price_and_string_qty_refuse():
    policy = _policy()
    bars = [
        _bar("AAA", 1_000, 10.0, 10.5),
        {"symbol": "AAA", "asof_ms": 2_000, "close": 11.5, "halted": False},
        _bar("AAA", 3_000, 12.0, 12.5),
    ]
    with pytest.raises(ConfigError, match="open"):
        ReplayAdapter(policy).replay(bars, [_decision("AAA", 1_000, lead=1)])
    ok_bars = [
        _bar("AAA", 1_000, 10.0, 10.5),
        _bar("AAA", 2_000, 11.0, 11.5),
        _bar("AAA", 3_000, 12.0, 12.5),
    ]
    with pytest.raises(ConfigError, match="qty"):
        ReplayAdapter(policy).replay(
            ok_bars, [_decision("AAA", 1_000, lead=1, qty="10")]
        )


def test_halted_unparseable_open_does_not_drop_a_peer_fill():
    policy = _policy()
    bars = [
        _bar("AAA", 1_000, 10.0, 10.5),
        {"symbol": "AAA", "asof_ms": 2_000, "open": None, "close": 11.5, "halted": True},
        _bar("BBB", 1_000, 20.0, 20.5),
        _bar("BBB", 2_000, 21.0, 21.5),
    ]
    out = ReplayAdapter(policy).replay(
        bars,
        [_decision("AAA", 1_000, lead=1), _decision("BBB", 1_000, lead=1)],
    )
    entries = [row for row in out["fills"] if row["kind"] == "entry"]
    assert [(row["symbol"], row["asof_ms"], row["price"]) for row in entries] == [
        ("BBB", 2_000, 21.0),
    ]
    assert any(row["reason"] == "halted" and row.get("symbol") == "AAA" for row in out["skipped"])


def test_empty_open_on_a_live_bar_raises():
    policy = _policy()
    bars = [
        _bar("AAA", 1_000, 10.0, 10.5),
        {"symbol": "AAA", "asof_ms": 2_000, "open": "", "close": 11.5, "halted": False},
        _bar("AAA", 3_000, 12.0, 12.5),
    ]
    with pytest.raises(ConfigError):
        ReplayAdapter(policy).replay(bars, [_decision("AAA", 1_000, lead=1)])


def test_non_finite_open_raises_instead_of_green_empty_peer():
    policy = _policy()
    bars = [
        _bar("AAA", 1_000, 10.0, 10.5),
        {"symbol": "AAA", "asof_ms": 2_000, "open": float("nan"), "close": 11.5, "halted": True},
        _bar("BBB", 1_000, 20.0, 20.5),
        _bar("BBB", 2_000, 21.0, 21.5),
    ]
    with pytest.raises(ConfigError, match="open"):
        ReplayAdapter(policy).replay(
            bars,
            [_decision("AAA", 1_000, lead=1), _decision("BBB", 1_000, lead=1)],
        )
    live = [
        _bar("AAA", 1_000, 10.0, 10.5),
        {"symbol": "AAA", "asof_ms": 2_000, "open": float("inf"), "close": 11.5, "halted": False},
        _bar("BBB", 1_000, 20.0, 20.5),
        _bar("BBB", 2_000, 21.0, 21.5),
    ]
    with pytest.raises(ConfigError, match="open"):
        ReplayAdapter(policy).replay(live, [_decision("BBB", 1_000, lead=1)])


def test_fill_suffix_fields_are_graded_and_friday_monday_closes_lead_390():
    raw = _raw_fill_policy()
    assert raw["fill_suffix_bars"] == raw["fill_bar_offset"] + 1170
    assert raw["fill_suffix_weekdays"] >= 3
    moved = dict(raw, fill_suffix_bars=raw["fill_suffix_bars"] + 1)
    assert FillPolicy(moved).digest() != FillPolicy(raw).digest()
    moved_days = dict(raw, fill_suffix_weekdays=raw["fill_suffix_weekdays"] + 1)
    assert FillPolicy(moved_days).digest() != FillPolicy(raw).digest()

    node = DevelopmentReplay("replay", {
        "deployment_eligible": False,
        "evidence_end": "2025-10-17",
        "fill_policy": "configs/fill-policy.json",
        "fill_policy_sha256": FillPolicy(raw).digest(),
        "caps": "development-only",
    })
    friday_last = int(
        datetime(2025, 10, 17, 19, 59, tzinfo=timezone.utc).timestamp() * 1000
    )
    monday = datetime(2025, 10, 20, 13, 30, tzinfo=timezone.utc)
    suffix = [
        _bar("AAA", int((monday + timedelta(minutes=i)).timestamp() * 1000),
             10.0 + i, 10.5 + i)
        for i in range(391)
    ]
    out = node.run(None, {
        "bars": [_bar("AAA", friday_last, 9.0, 9.5), *suffix],
        "decisions": [_decision("AAA", friday_last, lead=390)],
    })
    kinds = [row["kind"] for row in out["fills"]]
    assert kinds[0] == "entry"
    assert kinds[-1] == "exit"
    saturday = int(datetime(2025, 10, 18, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
    with pytest.raises(ConfigError, match="evidence_end"):
        node.run(None, {
            "bars": [
                _bar("AAA", friday_last, 9.0, 9.5),
                _bar("AAA", saturday, 11.0, 11.5),
            ],
            "decisions": [_decision("AAA", friday_last, lead=1)],
        })
    y2026 = 1_767_225_600_000
    with pytest.raises(ConfigError, match="evidence_end"):
        node.run(None, {
            "bars": [
                _bar("AAA", friday_last, 9.0, 9.5),
                _bar("AAA", y2026, 99.0, 99.5),
                _bar("AAA", y2026 + 60_000, 100.0, 100.5),
            ],
            "decisions": [_decision("AAA", friday_last, lead=1)],
        })


def test_mark_source_and_forced_exit_at_are_read():
    policy = _policy()
    bars = [
        _bar("AAA", 1_000, 10.0, 10.5),
        _bar("AAA", 2_000, 11.0, 11.5),
        _bar("AAA", 3_000, 12.0, 12.5),
    ]
    out = ReplayAdapter(policy).replay(bars, [_decision("AAA", 1_000, lead=1)])
    assert policy.mark_source == "fill_bar_open"
    assert policy.forced_exit_at == "horizon_expiry"
    exit_row = next(row for row in out["fills"] if row["kind"] == "exit")
    assert exit_row["price"] == pytest.approx(12.0)


# --- ADR-0176: a configured cash-flow schedule for the equity replay -----


def _raw_cash_flow_policy():
    with open(CASH_FLOW_POLICY_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _cash_flow_policy(overrides=None):
    payload = _raw_cash_flow_policy()
    if overrides:
        payload.update(overrides)
    return CashFlowPolicy(payload)


def test_shipped_cash_flow_policy_names_every_knob_and_hashes():
    raw = _raw_cash_flow_policy()
    policy = CashFlowPolicy.from_path(CASH_FLOW_POLICY_PATH)
    assert policy.currency == raw["currency"]
    assert policy.daily_contribution_amount == Decimal(raw["daily_contribution_amount"])
    assert policy.initial_capital_amount == Decimal(raw["initial_capital_amount"])
    assert policy.timezone.key == raw["timezone"]
    assert policy.digest() == CashFlowPolicy(raw).digest()


def test_cash_flow_policy_refuses_a_missing_or_unknown_knob():
    raw = _raw_cash_flow_policy()
    missing = dict(raw)
    del missing["daily_contribution_amount"]
    with pytest.raises(ConfigError, match="daily_contribution_amount"):
        CashFlowPolicy(missing)
    with pytest.raises(ConfigError, match="leverage"):
        CashFlowPolicy(dict(raw, leverage=2))


def test_cash_flow_policy_refuses_a_non_positive_or_unparseable_amount():
    with pytest.raises(ConfigError, match="daily_contribution_amount"):
        _cash_flow_policy({"daily_contribution_amount": "0"})
    with pytest.raises(ConfigError, match="daily_contribution_amount"):
        _cash_flow_policy({"daily_contribution_amount": "-5"})
    with pytest.raises(ConfigError, match="daily_contribution_amount"):
        _cash_flow_policy({"daily_contribution_amount": "not-a-number"})
    with pytest.raises(ConfigError, match="daily_contribution_amount"):
        _cash_flow_policy({"daily_contribution_amount": 20})
    with pytest.raises(ConfigError, match="initial_capital_amount"):
        _cash_flow_policy({"initial_capital_amount": "0"})


def test_cash_flow_policy_refuses_an_unknown_timezone_or_empty_currency():
    with pytest.raises(ConfigError, match="timezone"):
        _cash_flow_policy({"timezone": "Not/AZone"})
    with pytest.raises(ConfigError, match="currency"):
        _cash_flow_policy({"currency": ""})


def _every_local_date(policy, start_ms, days):
    """Every calendar date from ``start_ms``'s own local date, ``days`` long (ADR-0178 point 5)."""
    anchor = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).astimezone(policy.timezone)
    return frozenset(anchor.date() + timedelta(days=i) for i in range(days))


def test_composer_for_folds_initial_capital_into_the_first_daily_occurrence():
    policy = _cash_flow_policy()
    start_ms = int(datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc).timestamp() * 1000)
    composer, _funding_instants = policy.composer_for(
        "series-a", start_ms, _every_local_date(policy, start_ms, 4)
    )
    window_end = start_ms + 3 * 86_400_000 + 1
    due = composer.due(
        datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc),
        datetime.fromtimestamp(window_end / 1000, tz=timezone.utc),
    )
    amounts = [Decimal(record["body"]["amount"]) for record in due]
    assert amounts == [Decimal("1020"), Decimal("20"), Decimal("20"), Decimal("20")]
    assert all(record["body"]["currency"] == "USD" for record in due)
    assert all(record["kind"] == "cash_flow" for record in due)


def test_composer_for_refuses_an_empty_tape():
    policy = _cash_flow_policy()
    with pytest.raises(ConfigError, match="empty"):
        policy.composer_for("series-a", 0, frozenset())


def test_composer_for_materializes_safely_across_a_dst_transition():
    # 2026-03-08 is the US spring-forward date; anchor two days before at 09:30 ET.
    policy = _cash_flow_policy()
    start_ms = int(datetime(2026, 3, 6, 14, 30, tzinfo=timezone.utc).timestamp() * 1000)
    composer, _funding_instants = policy.composer_for(
        "series-b", start_ms, _every_local_date(policy, start_ms, 7)
    )
    window_end = start_ms + 6 * 86_400_000 + 1
    due = composer.due(
        datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc),
        datetime.fromtimestamp(window_end / 1000, tz=timezone.utc),
    )
    # Crosses the transition; every occurrence must be a real, ascending, positive-amount
    # instant -- proving safe materialization. .astimezone() always yields a valid instant,
    # so _valid_local's gap/fold ValueError is unreachable via this exact construction.
    instants = [record["body"]["effective_at_ms"] for record in due]
    assert instants == sorted(instants)
    assert len(instants) == len(set(instants)) == 7
    assert all(Decimal(record["body"]["amount"]) > 0 for record in due)


class _CapturingEquityReplay(EquityReplay):
    """Snapshot the run's own cash-flow ledger each tick, before ``_run_loop``'s cleanup."""

    def __init__(self, policy, cash_flow_policy):
        super().__init__(policy, cash_flow_policy)
        self.cash_flow_snapshots = []

    def read_entry(self, tick_at_ms):
        batch = super().read_entry(tick_at_ms)
        if self._cash_flow_ledger is not None:
            self.cash_flow_snapshots = [
                dict(envelope.get("body") or {})
                for envelope in self._cash_flow_ledger.scan(kind="cash_flow")
            ]
        return batch


class _CountingComposer:
    """Delegate ``due`` to the replay's real composer, counting only the replay's own calls.

    ``ReplayCashFlowComposer`` is slotted and immutable, and the ledger authorizer bound
    from it (``_authorizes``) also calls ``due`` once per appended record -- so a
    class-level patch would count the authorizer's calls too. Wrapping only the replay's
    own reference counts exactly the bookkeeping calls ADR-0178 bounds.
    """

    def __init__(self, inner):
        self._inner = inner
        self.calls = 0

    def due(self, start, end_exclusive):
        self.calls += 1
        return self._inner.due(start, end_exclusive)


class _WindowSpyingEquityReplay(EquityReplay):
    """Record every tick's cash-flow gate decision, window, and funding index (ADR-0176/0178).

    ``_CapturingEquityReplay`` (above) proves the final ledger content is right, but the
    ledger's own id-based idempotence (restart safety, ``dskit.production.cashflows``)
    silently absorbs a resubmission of an already-appended record -- so a final-state
    assertion alone cannot tell "window advanced correctly" apart from "window never
    advanced, but re-submitting the same records every tick was harmless". This subclass
    asserts the window sequence itself, independent of that safety net.

    ADR-0178 point 5.5: each tick also records whether its call reached ``due()``
    (``gated`` False) or returned at the funding-instant gate (``gated`` True), so a
    skipped tick is still checked, not invisible. The end-of-tape flush (point 1.6) runs
    after the last tick, where no per-tick snapshot can see it, so the flush's own
    ``due()`` calls, final funding index, and the ledger before/after it are recorded.
    """

    def __init__(self, policy, cash_flow_policy):
        super().__init__(policy, cash_flow_policy)
        self.ticks = []
        self.due_counter = None
        self.flush_due_calls = None
        self.flushed_index = None
        self.flushed_instants = None
        self.pre_flush_bodies = None
        self.cash_flow_bodies = None
        self.bookkeeping_seconds = 0.0

    def _due_calls(self):
        if self._cash_flow_composer is not None and self.due_counter is None:
            self.due_counter = _CountingComposer(self._cash_flow_composer)
            self._cash_flow_composer = self.due_counter
        return 0 if self.due_counter is None else self.due_counter.calls

    def _ledger_bodies(self):
        return [
            dict(envelope.get("body") or {})
            for envelope in self._cash_flow_ledger.scan(kind="cash_flow")
        ]

    def _submit_due_cash_flows(self, tick_at_ms):
        calls = self._due_calls()
        window_start_ms = self._cash_flow_window_ms
        index = getattr(self, "_cash_flow_funding_index", None)
        started = time.perf_counter()
        super()._submit_due_cash_flows(tick_at_ms)
        self.bookkeeping_seconds += time.perf_counter() - started
        if self._cash_flow_composer is None:
            return
        made = self._due_calls() - calls
        self.ticks.append({
            "at": tick_at_ms,
            "gated": made == 0,
            "due_calls": made,
            "window": (window_start_ms, self._cash_flow_window_ms),
            "index": (index, getattr(self, "_cash_flow_funding_index", None)),
        })

    def _flush_cash_flows(self):
        calls = self._due_calls()
        if self._cash_flow_ledger is not None:
            self.pre_flush_bodies = self._ledger_bodies()
        started = time.perf_counter()
        super()._flush_cash_flows()
        self.bookkeeping_seconds += time.perf_counter() - started
        self.flush_due_calls = self._due_calls() - calls
        self.flushed_index = self._cash_flow_funding_index
        self.flushed_instants = self._cash_flow_funding_instants
        if self._cash_flow_ledger is not None:
            self.cash_flow_bodies = self._ledger_bodies()


_CF_DAY0_MS = int(datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc).timestamp() * 1000)
_CF_DAY1_MS = _CF_DAY0_MS + 86_400_000
_CF_DAY2_MS = _CF_DAY0_MS + 2 * 86_400_000


def test_the_cash_flow_window_advances_only_on_ticks_that_reach_a_funding_instant():
    # ADR-0178 point 5.5: authorized rewrite of ADR-0176's
    # test_the_cash_flow_window_advances_by_exactly_one_tick_each_call. Funding instants
    # are DAY0 and DAY1 (the anchor's 09:30 ET on each trading date); a tick short of the
    # next unfunded instant is gated -- no due() call, window and index both stay put.
    policy = _policy()
    replay = _WindowSpyingEquityReplay(policy, _cash_flow_policy())
    bars = [
        _bar("AAA", _CF_DAY0_MS, 10.0, 10.1),
        _bar("AAA", _CF_DAY0_MS + 60_000, 10.1, 10.2),
        _bar("AAA", _CF_DAY1_MS, 11.0, 11.1),
        _bar("AAA", _CF_DAY1_MS + 60_000, 11.1, 11.2),
    ]
    replay.run(bars, [])
    assert [
        (tick["at"], tick["gated"], tick["due_calls"], tick["window"], tick["index"])
        for tick in replay.ticks
    ] == [
        (_CF_DAY0_MS, False, 1, (_CF_DAY0_MS, _CF_DAY0_MS + 1), (0, 1)),
        (_CF_DAY0_MS + 60_000, True, 0, (_CF_DAY0_MS + 1, _CF_DAY0_MS + 1), (1, 1)),
        (_CF_DAY1_MS, False, 1, (_CF_DAY0_MS + 1, _CF_DAY1_MS + 1), (1, 2)),
        (_CF_DAY1_MS + 60_000, True, 0, (_CF_DAY1_MS + 1, _CF_DAY1_MS + 1), (2, 2)),
    ]
    # Contiguous across the ticks that did call due(): each window's end is exactly the
    # next window's start -- no gap, no overlap.
    windows = [tick["window"] for tick in replay.ticks if not tick["gated"]]
    for (_, end), (next_start, _) in zip(windows, windows[1:]):
        assert end == next_start
    # The per-tick gate already funded both instants, so the flush is a no-op.
    assert replay.flush_due_calls == 0
    assert replay.flushed_instants == (_CF_DAY0_MS, _CF_DAY1_MS)
    assert replay.flushed_index == 2


def test_a_full_replay_submits_the_configured_cash_flows_into_its_own_ledger():
    policy = _policy()
    replay = _CapturingEquityReplay(policy, _cash_flow_policy())
    bars = [
        _bar("AAA", _CF_DAY0_MS, 10.0, 10.5),
        _bar("AAA", _CF_DAY1_MS, 11.0, 11.5),
        _bar("AAA", _CF_DAY2_MS, 12.0, 12.5),
    ]
    replay.run(bars, [])
    bodies = replay.cash_flow_snapshots
    assert len(bodies) == 3
    amounts = sorted(Decimal(body["amount"]) for body in bodies)
    assert amounts == [Decimal("20"), Decimal("20"), Decimal("1020")]
    assert all(body["currency"] == "USD" for body in bodies)
    # Every submitted record round-tripped through the real SeriesState._for_replay
    # authorizer bound from this exact composer -- ServeLoop would have failed the run
    # (surfaced as a raised error from _run_loop) had any submission been refused.


def test_only_one_cash_flow_record_lands_per_calendar_day_despite_many_ticks():
    policy = _policy()
    replay = _CapturingEquityReplay(policy, _cash_flow_policy())
    bars = [
        _bar("AAA", _CF_DAY0_MS, 10.0, 10.1),
        _bar("AAA", _CF_DAY0_MS + 60_000, 10.1, 10.2),
        _bar("AAA", _CF_DAY0_MS + 120_000, 10.2, 10.3),
        _bar("AAA", _CF_DAY1_MS, 11.0, 11.1),
        _bar("AAA", _CF_DAY1_MS + 60_000, 11.1, 11.2),
    ]
    replay.run(bars, [])
    bodies = replay.cash_flow_snapshots
    assert len(bodies) == 2
    assert sorted(Decimal(body["amount"]) for body in bodies) == [Decimal("20"), Decimal("1020")]


def test_a_replay_without_a_cash_flow_policy_submits_no_cash_flow_records():
    policy = _policy()
    replay = _CapturingEquityReplay(policy, None)
    bars = [
        _bar("AAA", _CF_DAY0_MS, 10.0, 10.5),
        _bar("AAA", _CF_DAY1_MS, 11.0, 11.5),
    ]
    replay.run(bars, [])
    assert replay.cash_flow_snapshots == []
    assert replay._cash_flow_ledger is None
    assert replay._cash_flow_composer is None


# --- ADR-0177: a buy entry the replay's own running balance cannot afford is refused ---


_ZERO_FEES = {"spread_bps": 0.0, "taf_per_share": 0.0, "sec31_bps": 0.0}


def _cf_bars(opens, symbol="AAA"):
    """One bar per minute from ``_CF_DAY0_MS``, one per entry of ``opens``."""
    return [
        _bar(symbol, _CF_DAY0_MS + i * 60_000, px, px + 0.5)
        for i, px in enumerate(opens)
    ]


def _cf_t(index):
    return _CF_DAY0_MS + index * 60_000


def _cash_reasons(out):
    return [row for row in out["refused"] if row["reason"] == "insufficient_cash"]


def test_an_unaffordable_buy_is_refused_opens_no_lot_and_queues_no_fill():
    # Balance after day-one funding: 1000 + 20 = 1020. 200 @ 10 = 2000 (+fee) > 1020.
    policy = _policy()
    bars = _cf_bars([10.0] * 6)
    out = ReplayAdapter(policy, _cash_flow_policy()).replay(bars, [
        _decision("AAA", _cf_t(0), lead=3, qty=200),
        _decision("AAA", _cf_t(1), lead=3, qty=50),
    ])
    assert out["refused"] == [{
        "symbol": "AAA", "asof_ms": _cf_t(1), "lead": 3, "reason": "insufficient_cash",
        "decision_ms": _cf_t(0),
    }]
    assert not any(row["qty"] == 200 for row in out["fills"])
    # A stale lot from the refused decision would expire at index 4 and make the
    # later same-lead decision (filling at index 2) refuse as same_lead_open.
    assert [(row["kind"], row["qty"], row["asof_ms"]) for row in out["fills"]] == [
        ("entry", 50, _cf_t(2)),
        ("exit", 50, _cf_t(5)),
    ]


@pytest.mark.parametrize(
    "fees, initial_capital, fills",
    [
        (_ZERO_FEES, "1000", True),      # cost 1020 == balance 1020
        (_ZERO_FEES, "999.99", False),   # cost 1020 == balance 1019.99 + 0.01
        ({}, "1000", False),             # 1020 notional + a non-zero buy fee > 1020
    ],
    ids=["cost-equals-balance", "cost-one-cent-over", "fee-counts-toward-cost"],
)
def test_the_insufficient_cash_boundary_is_cost_strictly_greater_than_balance(
    fees, initial_capital, fills,
):
    policy = _policy(fees)
    cash = _cash_flow_policy({"initial_capital_amount": initial_capital})
    bars = _cf_bars([10.0, 10.0, 10.0, 10.0])
    out = ReplayAdapter(policy, cash).replay(
        bars, [_decision("AAA", _cf_t(0), lead=1, qty=102)]
    )
    entries = [row for row in out["fills"] if row["kind"] == "entry"]
    if fills:
        assert [(row["qty"], row["asof_ms"]) for row in entries] == [(102, _cf_t(1))]
        assert _cash_reasons(out) == []
    else:
        assert entries == []
        assert _cash_reasons(out) == [{
            "symbol": "AAA", "asof_ms": _cf_t(1), "lead": 1, "reason": "insufficient_cash",
            "decision_ms": _cf_t(0),
        }]


def test_the_running_balance_tracks_every_fill_across_a_mixed_sequence():
    # 1020 funded; buy 100 @ 10 leaves ~19.78; a second 100 @ 10 is refused; the
    # lead-2 lot's forced exit sells 100 @ 15 (+~1499.6); a rebuy of 100 @ 15
    # (~1500.33) is affordable ONLY because the sell's full proceeds, profit
    # included, were credited.
    policy = _policy()
    bars = _cf_bars([10.0, 10.0, 10.0, 15.0, 15.0, 15.0])
    out = ReplayAdapter(policy, _cash_flow_policy()).replay(bars, [
        _decision("AAA", _cf_t(0), lead=2, qty=100),
        _decision("AAA", _cf_t(1), lead=1, qty=100),
        _decision("AAA", _cf_t(3), lead=1, qty=100),
    ])
    assert [
        (row["kind"], row["side"], row["lead"], row["asof_ms"]) for row in out["fills"]
    ] == [
        ("entry", "buy", 2, _cf_t(1)),
        ("exit", "sell", 2, _cf_t(3)),
        ("entry", "buy", 1, _cf_t(4)),
        ("exit", "sell", 1, _cf_t(5)),
    ]
    assert out["refused"] == [{
        "symbol": "AAA", "asof_ms": _cf_t(2), "lead": 1, "reason": "insufficient_cash",
        "decision_ms": _cf_t(1),
    }]


def test_a_short_entry_is_never_refused_for_insufficient_cash():
    # The first short's forced cover (100 @ 100) drives the balance to about
    # -7980 BEFORE the second short (500 @ 100, 50000 notional) enters on the
    # same bar; neither short is refused.
    policy = _policy()
    bars = _cf_bars([10.0, 10.0, 100.0, 100.0])
    out = ReplayAdapter(policy, _cash_flow_policy()).replay(bars, [
        _decision("AAA", _cf_t(0), lead=1, qty=100, side="sell"),
        _decision("AAA", _cf_t(1), lead=1, qty=500, side="sell"),
    ])
    assert [
        (row["kind"], row["side"], row["qty"], row["asof_ms"]) for row in out["fills"]
    ] == [
        ("entry", "sell", 100, _cf_t(1)),
        ("exit", "buy", 100, _cf_t(2)),
        ("entry", "sell", 500, _cf_t(2)),
        ("exit", "buy", 500, _cf_t(3)),
    ]
    assert out["refused"] == []


def test_a_forced_exit_always_executes_on_a_deeply_negative_balance():
    # Two shorts of 100 @ 10 fund ~3020; the lead-1 cover at 100 drives the
    # balance to about -6980; the lead-2 cover (10000 cost) still executes.
    policy = _policy()
    bars = _cf_bars([10.0, 10.0, 100.0, 100.0])
    out = ReplayAdapter(policy, _cash_flow_policy()).replay(bars, [
        _decision("AAA", _cf_t(0), lead=1, qty=100, side="sell"),
        _decision("AAA", _cf_t(0), lead=2, qty=100, side="sell"),
    ])
    exits = [
        (row["side"], row["lead"], row["qty"], row["asof_ms"])
        for row in out["fills"] if row["kind"] == "exit"
    ]
    assert exits == [("buy", 1, 100, _cf_t(2)), ("buy", 2, 100, _cf_t(3))]
    assert out["refused"] == []


def test_an_override_exit_always_executes_on_a_deeply_negative_balance():
    # The lead-1 cover at 100 drives the balance to about -7880; an override on
    # lead 3 must still buy back its open 10-share short (1000 cost) first.
    policy = _policy({"same_lead_overlap": "override"})
    bars = _cf_bars([10.0, 10.0, 100.0, 100.0, 100.0, 100.0])
    out = ReplayAdapter(policy, _cash_flow_policy()).replay(bars, [
        _decision("AAA", _cf_t(0), lead=1, qty=100, side="sell"),
        _decision("AAA", _cf_t(0), lead=3, qty=10, side="sell"),
        _decision("AAA", _cf_t(1), lead=3, qty=1, side="sell"),
    ])
    lead3 = [
        (row["kind"], row["side"], row["qty"], row["asof_ms"])
        for row in out["fills"] if row["lead"] == 3
    ]
    assert lead3 == [
        ("entry", "sell", 10, _cf_t(1)),
        ("exit", "buy", 10, _cf_t(2)),
        ("entry", "sell", 1, _cf_t(2)),
        ("exit", "buy", 1, _cf_t(5)),
    ]
    assert out["refused"] == []


def test_an_override_entry_refused_for_insufficient_cash_still_closes_the_prior_lot():
    # Accepted pre-existing sequencing (ADR-0177 Decision 4): the override exit is
    # queued before the new entry's own refusal, so the prior lot stays closed.
    policy = _policy({"same_lead_overlap": "override"})
    bars = _cf_bars([10.0, 10.0, 10.0, 10.0, 10.0])
    out = ReplayAdapter(policy, _cash_flow_policy()).replay(bars, [
        _decision("AAA", _cf_t(0), lead=2, qty=10, side="sell"),
        _decision("AAA", _cf_t(1), lead=2, qty=1000),
    ])
    assert [
        (row["kind"], row["side"], row["qty"], row["asof_ms"]) for row in out["fills"]
    ] == [
        ("entry", "sell", 10, _cf_t(1)),
        ("exit", "buy", 10, _cf_t(2)),
    ]
    # No lot survives: neither the prior short (natural expiry index 3) nor the
    # refused buy reaches the end-of-tape expiry_past_tape sweep.
    assert out["refused"] == [{
        "symbol": "AAA", "asof_ms": _cf_t(2), "lead": 2, "reason": "insufficient_cash",
        "decision_ms": _cf_t(1),
    }]


@pytest.mark.parametrize(
    "run",
    [
        lambda policy, bars, decisions: ReplayAdapter(policy).replay(bars, decisions),
        lambda policy, bars, decisions: EquityReplay(policy).run(bars, decisions),
    ],
    ids=["ReplayAdapter", "EquityReplay"],
)
def test_without_a_cash_flow_policy_an_unaffordable_buy_still_fills(run):
    policy = _policy()
    bars = _cf_bars([10.0, 10.0, 12.0])
    out = run(policy, bars, [_decision("AAA", _cf_t(0), lead=1, qty=1_000_000)])
    assert [
        (row["kind"], row["side"], row["qty"], row["price"], row["asof_ms"])
        for row in out["fills"]
    ] == [
        ("entry", "buy", 1_000_000, 10.0, _cf_t(1)),
        ("exit", "sell", 1_000_000, 12.0, _cf_t(2)),
    ]
    assert out["refused"] == []
    assert out["skipped"] == []


# --- ADR-0184 S1: exits before entries across every symbol of one tick -----


def test_same_bar_exit_on_later_symbol_funds_entry_on_earlier_symbol():
    # 1020 funded. BBB buys 100 @ 10 (balance 20) and force-exits at _cf_t(2);
    # AAA (inserted first, so iterated first) enters 100 @ 10 at _cf_t(2). The
    # buy is affordable ONLY with BBB's same-bar sale proceeds credited first.
    policy = _policy(_ZERO_FEES)
    bars = _cf_bars([10.0] * 4, symbol="AAA") + _cf_bars([10.0] * 4, symbol="BBB")
    out = ReplayAdapter(policy, _cash_flow_policy()).replay(bars, [
        _decision("BBB", _cf_t(0), lead=1, qty=100),
        _decision("AAA", _cf_t(1), lead=1, qty=100),
    ])
    assert _cash_reasons(out) == []
    assert [
        (row["kind"], row["symbol"], row["qty"], row["asof_ms"]) for row in out["fills"]
    ] == [
        ("entry", "BBB", 100, _cf_t(1)),
        ("entry", "AAA", 100, _cf_t(2)),
        ("exit", "BBB", 100, _cf_t(2)),
        ("exit", "AAA", 100, _cf_t(3)),
    ]


def test_halted_symbol_exit_skip_unchanged_by_two_pass():
    # AAA's lot expires on a halted bar: its exit is skipped there and taken at
    # the next live bar, while BBB's same-bar entry still fills normally.
    policy = _policy()
    bars = [
        _bar("AAA", 1_000, 10.0, 10.5),
        _bar("AAA", 2_000, 11.0, 11.5),
        _bar("AAA", 3_000, 12.0, 12.5, halted=True),
        _bar("AAA", 4_000, 13.0, 13.5),
        _bar("BBB", 2_000, 20.0, 20.5),
        _bar("BBB", 3_000, 21.0, 21.5),
        _bar("BBB", 4_000, 22.0, 22.5),
    ]
    out = ReplayAdapter(policy).replay(bars, [
        _decision("AAA", 1_000, lead=1),
        _decision("BBB", 2_000, lead=1),
    ])
    assert out["skipped"] == [
        {"symbol": "AAA", "asof_ms": 3_000, "lead": 1, "reason": "halted"}
    ]
    assert [
        (row["kind"], row["symbol"], row["asof_ms"], row["price"]) for row in out["fills"]
    ] == [
        ("entry", "AAA", 2_000, 11.0),
        ("entry", "BBB", 3_000, 21.0),
        ("exit", "AAA", 4_000, 13.0),
        ("exit", "BBB", 4_000, 22.0),
    ]
    assert out["refused"] == []


# --- ADR-0184 S2: BarTape is built in one pass over the bars ----------------


class _CountingBars(list):
    """A bar list that counts how many times it is iterated."""

    iterations = 0

    def __iter__(self):
        self.iterations += 1
        return super().__iter__()


def test_bar_tape_iterates_bars_a_bounded_number_of_times():
    bars = _CountingBars(
        _bar(symbol, 1_000 * (i + 1), 10.0, 10.5)
        for i in range(50)
        for symbol in ("AAA", "BBB")
    )
    bars.iterations = 0
    BarTape(bars, "src")
    assert bars.iterations <= 2


def test_bar_tape_records_added_per_timestamp():
    bars = [
        _bar("BBB", 2_000, 10.0, 10.5),
        _bar("AAA", 1_000, 10.0, 10.5),
        _bar("BBB", 1_000, 10.0, 10.5),
        _bar("AAA", 3_000, 10.0, 10.5),
        _bar("BBB", 3_000, 10.0, 10.5),
        _bar("CCC", 3_000, 10.0, 10.5),
    ]
    tape = BarTape(bars, "src")
    assert tape.start_ms() == 1_000
    assert [
        (r.status, r.acq_id, r.records_added, r.source_config_hash, r.at_ms)
        for r in tape.feed_results()
    ] == [
        ("live", "bar-1000", 2, "src", 1_000),
        ("live", "bar-2000", 1, "src", 2_000),
        ("live", "bar-3000", 3, "src", 3_000),
    ]
    assert BarTape([], "src").feed_results() == ()
    assert BarTape([], "src").start_ms() == 0


# --- ADR-0184 S6: the upfront-decisions path is unchanged by the decider hook


def test_run_with_explicit_decisions_unchanged():
    # Every upfront refusal, an insufficient-cash refusal at fill, a same-lead
    # refusal, and two funded round trips -- pinned literally, then required
    # byte-identical through the decider=None seam.
    policy = _policy(_ZERO_FEES)
    bars = (
        _cf_bars([10.0, 11.0, 12.0, 13.0, 14.0], symbol="AAA")
        + _cf_bars([20.0, 21.0, 22.0, 23.0, 24.0], symbol="BBB")
    )
    decisions = [
        _decision("AAA", _cf_t(0), lead=2, qty=5),
        _decision("BBB", _cf_t(0), lead=1, qty=100),
        _decision("ZZZ", _cf_t(0), lead=1),
        _decision("AAA", _cf_t(1), lead=0),
        _decision("AAA", _cf_t(0) + 30_000, lead=1),
        _decision("BBB", _cf_t(4), lead=1),
        _decision("AAA", _cf_t(1), lead=2, qty=1),
        _decision("BBB", _cf_t(2), lead=1, qty=10),
    ]
    expected = {
        "fills": [
            {"kind": "entry", "symbol": "AAA", "lead": 2, "side": "buy", "qty": 5,
             "price": 11.0, "asof_ms": _cf_t(1), "fee": 0.0, "decision_ms": _cf_t(0)},
            {"kind": "exit", "symbol": "AAA", "lead": 2, "side": "sell", "qty": 5,
             "price": 13.0, "asof_ms": _cf_t(3), "fee": 0.0, "reason": "horizon_expiry"},
            {"kind": "entry", "symbol": "BBB", "lead": 1, "side": "buy", "qty": 10,
             "price": 23.0, "asof_ms": _cf_t(3), "fee": 0.0, "decision_ms": _cf_t(2)},
            {"kind": "exit", "symbol": "BBB", "lead": 1, "side": "sell", "qty": 10,
             "price": 24.0, "asof_ms": _cf_t(4), "fee": 0.0, "reason": "horizon_expiry"},
        ],
        "skipped": [],
        "refused": [
            {"symbol": "ZZZ", "asof_ms": _cf_t(0), "lead": 1, "reason": "unknown_symbol",
             "decision_ms": _cf_t(0)},
            {"symbol": "AAA", "asof_ms": _cf_t(1), "lead": 0, "reason": "lead",
             "decision_ms": _cf_t(1)},
            {"symbol": "AAA", "asof_ms": _cf_t(0) + 30_000, "lead": 1,
             "reason": "unknown_decision_bar", "decision_ms": _cf_t(0) + 30_000},
            {"symbol": "BBB", "asof_ms": _cf_t(4), "lead": 1, "reason": "fill_bar_past_tape",
             "decision_ms": _cf_t(4)},
            {"symbol": "BBB", "asof_ms": _cf_t(1), "lead": 1, "reason": "insufficient_cash",
             "decision_ms": _cf_t(0)},
            {"symbol": "AAA", "asof_ms": _cf_t(2), "lead": 2, "reason": "same_lead_open",
             "decision_ms": _cf_t(1)},
        ],
    }
    cash = _cash_flow_policy()
    out = EquityReplay(policy, cash).run(copy.deepcopy(bars), copy.deepcopy(decisions))
    # ADR-0183 item 13 adds the funded run's cash_flows + cash outputs; the
    # three pinned outputs are unchanged by them.
    assert set(out) == set(expected) | {"cash_flows", "cash"}
    assert {key: out[key] for key in expected} == expected
    hooked = EquityReplay(policy, cash, decider=None).run(
        copy.deepcopy(bars), copy.deepcopy(decisions)
    )
    assert json.dumps(hooked, sort_keys=True) == json.dumps(out, sort_keys=True)
    adapted = ReplayAdapter(policy, cash).replay(bars, decisions)
    assert {key: adapted[key] for key in expected} == expected


# --- ADR-0184 S7(d): one runtime-inventory read per replay run, not per tick


def test_replay_reads_the_runtime_inventory_once_per_run_not_per_tick(monkeypatch):
    from importlib import metadata

    from dskit.production.release import RuntimeFingerprint

    reads = []
    real = metadata.PathDistribution.read_text

    def spy(self, filename):
        reads.append(filename)
        return real(self, filename)

    monkeypatch.setattr(metadata.PathDistribution, "read_text", spy)
    RuntimeFingerprint.capture()
    once = len(reads)
    reads.clear()
    bars = _cf_bars([10.0 + i for i in range(12)])
    out = EquityReplay(_policy(), _cash_flow_policy()).run(bars, [_decision("AAA", _cf_t(0), 2, qty=1)])
    assert [f["kind"] for f in out["fills"]] == ["entry", "exit"]
    # The release's own capture plus twelve per-tick re-verifications would
    # be thirteen reads of the inventory; the replay makes exactly one.
    assert once > 0 and len(reads) == once
    # Outside the replay the production path re-reads on every capture.
    RuntimeFingerprint.capture()
    assert len(reads) == 2 * once


# --- ADR-0178: market-calendar-aware cash-flow contribution timing ---------


_CF_TZ = CashFlowPolicy.from_path(CASH_FLOW_POLICY_PATH).timezone


def _local_ms(year, month, day, hour, minute):
    """Epoch ms of one wall-clock instant in the shipped cash-flow policy's own timezone."""
    return int(datetime(year, month, day, hour, minute, tzinfo=_CF_TZ).timestamp() * 1000)


def _utc(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _funded(bodies):
    """``(effective_at_ms, amount)`` per cash-flow record, in effective order."""
    return sorted(
        (body["effective_at_ms"], Decimal(body["amount"])) for body in bodies
    )


def test_composer_for_skips_non_trading_dates_and_returns_one_instant_per_trading_date():
    policy = _cash_flow_policy()
    friday = _local_ms(2026, 1, 9, 9, 30)
    monday = _local_ms(2026, 1, 12, 9, 30)
    composer, instants = policy.composer_for(
        "series-c", friday, frozenset({date(2026, 1, 9), date(2026, 1, 12)})
    )
    assert instants == (friday, monday)
    due = composer.due(_utc(friday), _utc(monday + 1))
    assert _funded(record["body"] for record in due) == [
        (friday, Decimal("1020")), (monday, Decimal("20")),
    ]


def test_a_weekend_is_skipped_friday_funds_the_seed_and_monday_only_its_own_twenty():
    friday = _local_ms(2026, 1, 9, 9, 30)
    monday = _local_ms(2026, 1, 12, 9, 30)
    replay = _CapturingEquityReplay(_policy(), _cash_flow_policy())
    replay.run([
        _bar("AAA", friday, 10.0, 10.1),
        _bar("AAA", friday + 60_000, 10.1, 10.2),
        _bar("AAA", monday, 11.0, 11.1),
        _bar("AAA", monday + 60_000, 11.1, 11.2),
    ], [])
    # No Saturday/Sunday record, and Monday is exactly $20 -- not $60 of weekend backlog.
    assert _funded(replay.cash_flow_snapshots) == [
        (friday, Decimal("1020")), (monday, Decimal("20")),
    ]
    assert replay._cash_balance == Decimal("1040")


def test_a_single_mid_week_gap_date_is_skipped_and_the_next_day_funds_only_itself():
    tuesday = _local_ms(2026, 1, 6, 9, 30)
    thursday = _local_ms(2026, 1, 8, 9, 30)
    replay = _CapturingEquityReplay(_policy(), _cash_flow_policy())
    replay.run([
        _bar("AAA", tuesday, 10.0, 10.1),
        _bar("AAA", tuesday + 60_000, 10.1, 10.2),
        _bar("AAA", thursday, 11.0, 11.1),
    ], [])
    assert _funded(replay.cash_flow_snapshots) == [
        (tuesday, Decimal("1020")), (thursday, Decimal("20")),
    ]


def test_the_anchor_date_is_never_skipped_even_on_a_saturday_past_utc_midnight():
    # Saturday 19:30 local is already Sunday in UTC: a weekday-based or UTC-dated
    # trading-day rule would each mistake the anchor's own date for a non-trading one.
    saturday = _local_ms(2026, 1, 10, 19, 30)
    monday = _local_ms(2026, 1, 12, 19, 30)
    assert _utc(saturday).astimezone(_CF_TZ).weekday() == 5
    assert _utc(saturday).date() != _utc(saturday).astimezone(_CF_TZ).date()
    replay = _CapturingEquityReplay(_policy(), _cash_flow_policy())
    replay.run([
        _bar("AAA", saturday, 10.0, 10.1),
        _bar("AAA", monday, 11.0, 11.1),
    ], [])
    assert _funded(replay.cash_flow_snapshots) == [
        (saturday, Decimal("1020")), (monday, Decimal("20")),
    ]


def test_a_dense_gap_free_tape_funds_exactly_as_adr_0176_did():
    # Regression baseline: every calendar date has a bar, so nothing is skipped and the
    # ledger is one record per calendar day at the anchor's local time, as before.
    days = [_local_ms(2026, 1, 5 + i, 9, 30) for i in range(7)]
    bars = []
    for day in days:
        bars.append(_bar("AAA", day, 10.0, 10.1))
        bars.append(_bar("AAA", day + 60_000, 10.1, 10.2))
    replay = _CapturingEquityReplay(_policy(), _cash_flow_policy())
    replay.run(bars, [])
    assert _funded(replay.cash_flow_snapshots) == (
        [(days[0], Decimal("1020"))] + [(day, Decimal("20")) for day in days[1:]]
    )


def test_a_later_tick_on_the_same_day_funds_an_instant_its_first_tick_missed():
    # Point 1.5 (the Revision-2 counter-scenario): anchor Monday 10:00; Tuesday and
    # Wednesday open at 09:30, before their own 10:00 instant, but each has a 10:30 bar
    # that reaches it. The per-tick gate alone funds all three days; the flush is a no-op.
    monday = _local_ms(2026, 1, 5, 10, 0)
    tuesday = _local_ms(2026, 1, 6, 10, 0)
    wednesday = _local_ms(2026, 1, 7, 10, 0)
    replay = _WindowSpyingEquityReplay(_policy(), _cash_flow_policy())
    replay.run([
        _bar("AAA", monday, 10.0, 10.1),
        _bar("AAA", tuesday - 30 * 60_000, 10.1, 10.2),
        _bar("AAA", tuesday + 30 * 60_000, 10.2, 10.3),
        _bar("AAA", wednesday - 30 * 60_000, 10.3, 10.4),
        _bar("AAA", wednesday + 30 * 60_000, 10.4, 10.5),
    ], [])
    assert [tick["gated"] for tick in replay.ticks] == [False, True, False, True, False]
    assert replay.ticks[-1]["index"] == (2, 3)
    assert replay.flush_due_calls == 0
    assert replay.flushed_instants == (monday, tuesday, wednesday)
    assert replay.flushed_index == len(replay.flushed_instants)
    assert _funded(replay.cash_flow_bodies) == [
        (monday, Decimal("1020")), (tuesday, Decimal("20")), (wednesday, Decimal("20")),
    ]
    assert replay.pre_flush_bodies == replay.cash_flow_bodies


def test_the_end_of_tape_flush_funds_an_early_close_last_day_no_tick_reaches():
    # Point 1.6 (the Revision-3 counter-scenario): anchor Monday 13:01; Tuesday is a
    # normal session; Wednesday -- the tape's last day -- closes early with every bar at
    # or before 13:00, so no tick ever reaches Wednesday's 13:01 instant. Only the flush
    # can fund it.
    monday = _local_ms(2026, 1, 5, 13, 1)
    tuesday = _local_ms(2026, 1, 6, 13, 1)
    wednesday = _local_ms(2026, 1, 7, 13, 1)
    wednesday_bars = [
        _local_ms(2026, 1, 7, 9, 30), _local_ms(2026, 1, 7, 12, 0), _local_ms(2026, 1, 7, 13, 0),
    ]
    replay = _WindowSpyingEquityReplay(_policy(), _cash_flow_policy())
    replay.run([
        _bar("AAA", monday, 10.0, 10.1),
        _bar("AAA", _local_ms(2026, 1, 5, 15, 59), 10.1, 10.2),
        _bar("AAA", _local_ms(2026, 1, 6, 9, 30), 10.2, 10.3),
        _bar("AAA", tuesday, 10.3, 10.4),
        _bar("AAA", _local_ms(2026, 1, 6, 15, 59), 10.4, 10.5),
    ] + [_bar("AAA", at, 10.5, 10.6) for at in wednesday_bars], [])
    assert all(at < wednesday for at in wednesday_bars)
    assert [tick["gated"] for tick in replay.ticks] == [
        False, True, True, False, True, True, True, True,
    ]
    # Before the flush Wednesday is unfunded and its instant is still pending ...
    assert replay.ticks[-1]["index"] == (2, 2)
    assert wednesday not in [at for at, _ in _funded(replay.pre_flush_bodies)]
    # ... and the flush alone funds it, with exactly one due() call.
    assert replay.flush_due_calls == 1
    assert replay.flushed_instants == (monday, tuesday, wednesday)
    assert replay.flushed_index == len(replay.flushed_instants)
    assert _funded(replay.cash_flow_bodies) == [
        (monday, Decimal("1020")), (tuesday, Decimal("20")), (wednesday, Decimal("20")),
    ]
    assert replay._cash_balance == Decimal("1060")


def _full_year_trading_dates():
    """2026 weekdays from Jan 2 through Dec 31, less a representative exchange-holiday set."""
    holidays = {
        date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25),
        date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26),
        date(2026, 12, 25),
    }
    day, last, found = date(2026, 1, 2), date(2026, 12, 31), []
    while day <= last:
        if day.weekday() < 5 and day not in holidays:
            found.append(day)
        day += timedelta(days=1)
    return found


def test_due_is_called_once_per_trading_date_not_once_per_tick_over_a_full_year():
    # Scale row: a full year of trading dates, so ~113 SkipCashFlow overrides (weekends
    # plus holidays) sit on the real RecurringCashFlowSchedule. A market-open anchor means
    # every day's first tick reaches its own instant, so due() fires exactly once per
    # trading date and the flush adds nothing. ServeLoop itself costs ~30 ms/tick, so
    # the tape carries three bars a day; the due() count is independent of that density.
    trading = _full_year_trading_dates()
    assert (trading[-1] - trading[0]).days + 1 - len(trading) >= 60
    bars = [
        _bar("AAA", _local_ms(day.year, day.month, day.day, hour, minute), 10.0, 10.1)
        for day in trading
        for hour, minute in ((9, 30), (12, 45), (15, 59))
    ]
    cash_flow_policy = _cash_flow_policy()
    replay = _WindowSpyingEquityReplay(_policy(), cash_flow_policy)
    started = time.perf_counter()
    replay.run(bars, [])
    elapsed = time.perf_counter() - started
    calls = replay.due_counter.calls
    print(
        f"\nADR-0178 scale: {len(bars)} ticks, {len(trading)} trading dates, "
        f"{calls} due() calls, flush {replay.flush_due_calls}, "
        f"cash-flow bookkeeping {replay.bookkeeping_seconds:.2f}s, run {elapsed:.2f}s"
    )
    assert len(replay.ticks) == len(bars)
    assert calls <= len(trading) + 1
    assert calls == len(trading)
    assert replay.flush_due_calls == 0
    assert replay.flushed_index == len(replay.flushed_instants) == len(trading)
    assert replay._cash_balance == (
        cash_flow_policy.initial_capital_amount
        + cash_flow_policy.daily_contribution_amount * len(trading)
    )
    # Generous, honest ceiling for the cash-flow bookkeeping alone (ADR-0178 scale row).
    assert replay.bookkeeping_seconds < 60


# --- ADR-0179: cash_flow_policy wired through DevelopmentReplay -----------


_CF_EVIDENCE_END = "2026-01-05"  # the UTC date of _CF_DAY0_MS, so _cf_bars stay in-window
_MALFORMED_CASH_FLOW_POLICY_PATHS = (5, [], "")


def _shipped_replay_params(**overrides):
    """The shipped run-development-replay.json node params, optionally overridden."""
    document = load_document(os.path.join(CONFIGS, "run-development-replay.json"))
    params = dict(document.pipeline["replay"].params)
    params.update(overrides)
    return params


def _cash_flow_pair(path="configs/cash-flow-policy.json"):
    return {
        "cash_flow_policy": path,
        "cash_flow_policy_sha256": CashFlowPolicy.from_path(CASH_FLOW_POLICY_PATH).digest(),
    }


def test_development_replay_keeps_five_required_params_and_its_optional_ones():
    assert DevelopmentReplay._PARAMS == (
        "deployment_eligible",
        "evidence_end",
        "fill_policy",
        "fill_policy_sha256",
        "caps",
    )
    assert DevelopmentReplay._OPTIONAL_PARAMS == (
        "cash_flow_policy",
        "cash_flow_policy_sha256",
        "keep_ledger",
        "guards",
    )


def test_the_shipped_document_still_runs_without_a_cash_flow_policy_byte_identically():
    params = _shipped_replay_params()
    assert "cash_flow_policy" not in params
    assert "cash_flow_policy_sha256" not in params
    assert DevelopmentReplay.validate_params(params) == []
    node = DevelopmentReplay("replay", params)
    assert node._cash_flow_policy is None
    last_day = 1_760_572_800_000  # 2025-10-16T00:00:00Z
    fill_only = 1_760_659_200_000  # 2025-10-17T00:00:00Z exclusive end
    bars = [
        _bar("AAA", last_day, 10.0, 10.5),
        _bar("AAA", fill_only, 11.0, 11.5),
        _bar("AAA", fill_only + 60_000, 12.0, 12.5),
    ]
    decisions = [_decision("AAA", last_day, lead=1, qty=1_000_000)]
    out = node.run(None, {"bars": bars, "decisions": decisions})
    before = ReplayAdapter(FillPolicy.from_path(FILL_POLICY_PATH)).replay(bars, decisions)
    assert json.dumps(out, sort_keys=True) == json.dumps(before, sort_keys=True)
    assert out["refused"] == []


@pytest.mark.parametrize(
    "path",
    ["configs/cash-flow-policy.json", CASH_FLOW_POLICY_PATH],
    ids=["relative-to-child-root", "absolute"],
)
def test_a_valid_cash_flow_pair_validates_and_builds_the_policy(path):
    params = _shipped_replay_params(**_cash_flow_pair(path))
    assert DevelopmentReplay.validate_params(params) == []
    node = DevelopmentReplay("replay", params)
    assert isinstance(node._cash_flow_policy, CashFlowPolicy)
    assert node._cash_flow_policy.digest() == params["cash_flow_policy_sha256"]


def test_run_passes_the_cash_flow_policy_through_so_an_unaffordable_buy_refuses():
    # Balance after day-one funding: 1000 + 20 = 1020. 200 @ 10 = 2000 (+fee) > 1020.
    # Without the policy threaded into ReplayAdapter, no balance exists and it fills.
    bars = _cf_bars([10.0] * 6)
    decisions = [
        _decision("AAA", _cf_t(0), lead=3, qty=200),
        _decision("AAA", _cf_t(1), lead=3, qty=50),
    ]
    funded = DevelopmentReplay(
        "replay", _shipped_replay_params(evidence_end=_CF_EVIDENCE_END, **_cash_flow_pair())
    ).run(None, {"bars": bars, "decisions": decisions})
    assert funded["refused"] == [{
        "symbol": "AAA", "asof_ms": _cf_t(1), "lead": 3, "reason": "insufficient_cash",
        "decision_ms": _cf_t(0),
    }]
    assert [(row["kind"], row["qty"], row["asof_ms"]) for row in funded["fills"]] == [
        ("entry", 50, _cf_t(2)),
        ("exit", 50, _cf_t(5)),
    ]
    direct = ReplayAdapter(_policy(), _cash_flow_policy()).replay(bars, decisions)
    assert json.dumps(funded, sort_keys=True) == json.dumps(direct, sort_keys=True)
    unfunded = DevelopmentReplay(
        "replay", _shipped_replay_params(evidence_end=_CF_EVIDENCE_END)
    ).run(None, {"bars": bars, "decisions": decisions})
    assert _cash_reasons(unfunded) == []
    assert any(row["qty"] == 200 for row in unfunded["fills"])


@pytest.mark.parametrize("present", ["cash_flow_policy", "cash_flow_policy_sha256"])
def test_one_half_of_the_cash_flow_pair_alone_refuses_naming_both(present):
    params = _shipped_replay_params(**{present: _cash_flow_pair()[present]})
    message = (
        "cash_flow_policy and cash_flow_policy_sha256 must both be present or both be absent"
    )
    assert DevelopmentReplay.validate_params(params) == [message]
    with pytest.raises(ConfigError) as caught:
        DevelopmentReplay("replay", params)
    assert caught.value.errors == [f"replay: {message}"]


@pytest.mark.parametrize(
    "value", _MALFORMED_CASH_FLOW_POLICY_PATHS, ids=["int", "list", "empty"]
)
def test_a_malformed_cash_flow_policy_refuses_cleanly_never_a_type_error(value):
    params = _shipped_replay_params(**dict(_cash_flow_pair(), cash_flow_policy=value))
    assert DevelopmentReplay.validate_params(params) == [
        "cash_flow_policy must be a non-empty path"
    ]
    with pytest.raises(ConfigError) as caught:
        DevelopmentReplay("replay", params)
    assert caught.value.errors == ["replay: cash_flow_policy must be a non-empty path"]


def test_a_missing_cash_flow_policy_path_refuses():
    params = _shipped_replay_params(**_cash_flow_pair("configs/no-such-cash-flow-policy.json"))
    expected = [
        "cash_flow_policy path does not exist: "
        + os.path.join(CHILD_ROOT, "configs", "no-such-cash-flow-policy.json")
    ]
    assert DevelopmentReplay.validate_params(params) == expected
    with pytest.raises(ConfigError) as caught:
        DevelopmentReplay("replay", params)
    assert caught.value.errors == [f"replay: {expected[0]}"]


@pytest.mark.parametrize(
    "digest, expected",
    [
        ("0" * 64, "cash_flow_policy_sha256 does not match the loaded cash-flow-policy digest"),
        (123, "cash_flow_policy_sha256 must be the cash-flow-policy digest"),
    ],
    ids=["mismatch", "non-string"],
)
def test_a_wrong_cash_flow_policy_digest_refuses(digest, expected):
    params = _shipped_replay_params(
        **dict(_cash_flow_pair(), cash_flow_policy_sha256=digest)
    )
    assert DevelopmentReplay.validate_params(params) == [expected]
    with pytest.raises(ConfigError) as caught:
        DevelopmentReplay("replay", params)
    assert caught.value.errors == [f"replay: {expected}"]


# --- ADR-0183 item 13: the policy's cash flows and the running balance as outputs ---


def test_cash_flows_are_emitted_as_submitted_one_row_per_record():
    policy = _policy()
    bars = [
        _bar("AAA", _CF_DAY0_MS, 10.0, 10.1),
        _bar("AAA", _CF_DAY0_MS + 60_000, 10.1, 10.2),
        _bar("AAA", _CF_DAY1_MS, 11.0, 11.1),
    ]
    replay = _CapturingEquityReplay(policy, _cash_flow_policy())
    out = replay.run(bars, [])
    submitted = replay.cash_flow_snapshots
    assert [
        (row["asof_ms"], Decimal(str(row["amount"])), row["currency"])
        for row in out["cash_flows"]
    ] == [
        (body["effective_at_ms"], Decimal(body["amount"]), body["currency"])
        for body in submitted
    ]
    assert [row["rule"] for row in out["cash_flows"]] == [
        "initial_capital",
        "daily_contribution",
    ]
    assert [row["amount"] for row in out["cash_flows"]] == [1020.0, 20.0]
    assert all(row["detail"].startswith("deposit: ") for row in out["cash_flows"])
    # With no fills, the balance is the running sum of the deposits.
    assert [row["cash"] for row in out["cash"]] == [1020.0, 1040.0]
    assert [row["asof_ms"] for row in out["cash"]] == [
        row["asof_ms"] for row in out["cash_flows"]
    ]


def test_the_cash_series_tracks_fills_exactly_one_row_per_instant():
    policy = _policy(_ZERO_FEES)
    bars = _cf_bars([10.0, 10.0, 12.0, 12.0])
    out = ReplayAdapter(policy, _cash_flow_policy()).replay(
        bars, [_decision("AAA", _cf_t(0), lead=1, qty=10)]
    )
    # 1020 funded; buy 10 @ 10 at t1; the lead-1 lot exits 10 @ 12 at t2.
    assert out["cash"] == [
        {"asof_ms": out["cash_flows"][0]["asof_ms"], "cash": 1020.0},
        {"asof_ms": _cf_t(1), "cash": 920.0},
        {"asof_ms": _cf_t(2), "cash": 1040.0},
    ]


def test_an_unfunded_replay_emits_no_cash_flows_and_no_cash_series():
    out = ReplayAdapter(_policy()).replay(
        _cf_bars([10.0, 10.0, 10.0]), [_decision("AAA", _cf_t(0), lead=1, qty=1)]
    )
    assert out["cash_flows"] == []
    assert out["cash"] == []


def test_development_replay_declares_and_returns_the_cash_outputs():
    assert DevelopmentReplay.outputs == (
        "fills", "skipped", "refused", "cash_flows", "cash", "findings", "ledger",
    )
    bars = _cf_bars([10.0] * 4)
    out = DevelopmentReplay(
        "replay", _shipped_replay_params(evidence_end=_CF_EVIDENCE_END, **_cash_flow_pair())
    ).run(None, {"bars": bars, "decisions": [_decision("AAA", _cf_t(0), lead=1, qty=1)]})
    assert set(out) == set(DevelopmentReplay.outputs)
    assert [row["rule"] for row in out["cash_flows"]] == ["initial_capital"]


def test_fills_name_their_decision_bar_and_exits_name_their_reason():
    policy = _policy()
    bars = _cf_bars([10.0, 10.0, 10.0, 10.0])
    out = ReplayAdapter(policy).replay(bars, [_decision("AAA", _cf_t(0), lead=2, qty=1)])
    entry = next(row for row in out["fills"] if row["kind"] == "entry")
    exit_ = next(row for row in out["fills"] if row["kind"] == "exit")
    assert entry["decision_ms"] == _cf_t(0)
    assert "reason" not in entry
    assert exit_["reason"] == policy.forced_exit_at == "horizon_expiry"
    assert "decision_ms" not in exit_


def test_an_override_exit_names_the_decision_that_forced_it():
    policy = _policy({"same_lead_overlap": "override"})
    bars = _cf_bars([10.0] * 5)
    out = ReplayAdapter(policy).replay(bars, [
        _decision("AAA", _cf_t(0), lead=3, qty=1),
        _decision("AAA", _cf_t(1), lead=3, qty=2),
    ])
    override = next(
        row for row in out["fills"]
        if row["kind"] == "exit" and row.get("reason") == "same_lead_override"
    )
    assert override["decision_ms"] == _cf_t(1)
    assert override["asof_ms"] == _cf_t(2)


def test_decision_stage_refusals_name_their_decision_bar():
    out = ReplayAdapter(_policy()).replay(
        _cf_bars([10.0, 10.0]), [_decision("ZZZ", _cf_t(0), lead=1)]
    )
    assert out["refused"] == [{
        "symbol": "ZZZ", "asof_ms": _cf_t(0), "lead": 1, "reason": "unknown_symbol",
        "decision_ms": _cf_t(0),
    }]


# --- ADR-0183 phase 2 item 3: ledger findings, the kept ledger, guards


def _observational_guards(max_qty="1000000", max_notional="100000000"):
    """Two limits too generous to breach: they record, they never refuse."""
    return {
        "size": {"uses": "limit", "params": {
            "measure": "quantity", "bound": {"max": max_qty}, "on_breach": "refuse",
        }},
        "notional": {"uses": "limit", "params": {
            "measure": "notional", "bound": {"max": max_notional}, "on_breach": "refuse",
        }},
    }


def _findings_tape():
    return _cf_bars([10.0, 11.0, 12.0, 13.0]), [_decision("AAA", _cf_t(0), lead=2, qty=5)]


def _read_back(root, series_id):
    from dskit.production.clock import TestClock
    from dskit.production.ledger import JsonlLedger, ServeRoot

    return JsonlLedger.reading(ServeRoot(str(root), series_id), clock=TestClock(0))


_RUN_KEYS = ("fills", "skipped", "refused", "cash_flows", "cash")


def test_observational_limits_record_one_findings_row_per_fill():
    bars, decisions = _findings_tape()
    replay = EquityReplay(_policy(_ZERO_FEES), guards=_observational_guards())
    out = replay.run(copy.deepcopy(bars), copy.deepcopy(decisions))
    # The run() dict is unchanged: findings ride on the object.
    assert set(out) == set(_RUN_KEYS)
    plain = EquityReplay(_policy(_ZERO_FEES)).run(copy.deepcopy(bars), copy.deepcopy(decisions))
    assert out == plain
    assert len(out["fills"]) == 2
    keys = [(r["symbol"], r["lead"], r["kind"], r["asof_ms"]) for r in replay.findings]
    assert keys == [(f["symbol"], f["lead"], f["kind"], f["asof_ms"]) for f in out["fills"]]
    for row, fill in zip(replay.findings, out["fills"]):
        assert set(row) == {
            "symbol", "lead", "kind", "asof_ms", "tick_id", "leg_id", "verdict", "findings",
        }
        assert row["verdict"] == "allow"
        assert isinstance(row["tick_id"], str) and isinstance(row["leg_id"], str)
        measured = {
            f["guard"]: (f["measure"], Decimal(f["value"]), f["bound"], f["verdict"])
            for f in row["findings"]
        }
        assert measured == {
            "size": ("quantity", Decimal(fill["qty"]), "1000000", "allow"),
            "notional": (
                "notional", Decimal(str(fill["price"])) * fill["qty"], "100000000", "allow",
            ),
        }


def test_a_replay_without_guards_has_no_findings_rows():
    bars, decisions = _findings_tape()
    replay = EquityReplay(_policy(_ZERO_FEES))
    replay.run(bars, decisions)
    assert replay.findings == []


def test_the_ledger_is_copied_to_ledger_dir_and_reads_back_at_the_same_head(tmp_path):
    from dskit.production.reconcile import LedgerHistory

    bars, decisions = _findings_tape()
    target = tmp_path / "kept"
    replay = EquityReplay(
        _policy(_ZERO_FEES), guards=_observational_guards(), ledger_dir=str(target),
    )
    replay.run(bars, decisions)
    segments = list((target / replay.series_id / "ledger").iterdir())
    assert segments and all(path.stat().st_size > 0 for path in segments)
    reader = _read_back(target, replay.series_id)
    seq, head = replay.ledger_head
    assert seq > 0 and reader.head() == (seq, head)
    assert reader.verify() is None
    kept = LedgerHistory(reader).leg_findings(0)
    assert [row["leg_id"] for row in kept] == [row["leg_id"] for row in replay.findings]


def test_an_existing_non_empty_ledger_dir_is_refused_before_the_replay_runs(tmp_path):
    bars, decisions = _findings_tape()
    target = tmp_path / "kept"
    target.mkdir()
    (target / "stale").write_text("x", encoding="utf-8")
    replay = EquityReplay(_policy(_ZERO_FEES), ledger_dir=str(target))
    with pytest.raises(ConfigError, match="ledger_dir"):
        replay.run(bars, decisions)
    assert replay.fills == []
    assert sorted(path.name for path in target.iterdir()) == ["stale"]


def test_a_guard_breach_still_fails_the_replay_loudly():
    bars, decisions = _findings_tape()
    replay = EquityReplay(_policy(_ZERO_FEES), guards=_observational_guards(max_qty="1"))
    with pytest.raises(ConfigError, match="queued but never submitted"):
        replay.run(bars, decisions)
    # Every refused leg is still reported, with no fill to join to.
    assert replay.findings
    for row in replay.findings:
        assert row["verdict"] == "refuse"
        assert (row["symbol"], row["lead"], row["kind"], row["asof_ms"]) == (None,) * 4
        assert {f["guard"] for f in row["findings"] if f["verdict"] == "refuse"} == {"size"}


def test_an_absent_guard_map_leaves_the_serve_document_unchanged():
    from intraday_equities.replay import _serve_document

    base = _serve_document("run", "series-1", ["AAA"], [1, 2])
    assert base["guards"] == {}
    assert _serve_document("run", "series-1", ["AAA"], [1, 2], guards=None) == base
    guarded = _serve_document(
        "run", "series-1", ["AAA"], [1, 2], guards=_observational_guards(),
    )
    assert guarded["guards"] == _observational_guards()


def test_development_replay_returns_findings_and_keeps_its_ledger(tmp_path):
    from types import SimpleNamespace

    params = _shipped_replay_params(evidence_end=_CF_EVIDENCE_END)
    assert "guards" not in params and "keep_ledger" not in params
    bars, decisions = _findings_tape()
    plain = DevelopmentReplay("replay", params).run(None, {"bars": bars, "decisions": decisions})
    assert plain["findings"] == [] and plain["ledger"] is None
    kept = DevelopmentReplay("replay", dict(
        params, keep_ledger=True, guards=_observational_guards(),
    ))
    ctx = SimpleNamespace(run_dir=str(tmp_path))
    out = kept.run(ctx, {"bars": bars, "decisions": decisions})
    assert set(out) == set(DevelopmentReplay.outputs)
    assert {key: out[key] for key in _RUN_KEYS} == {key: plain[key] for key in _RUN_KEYS}
    assert [row["verdict"] for row in out["findings"]] == ["allow"] * len(out["fills"])
    ledger = out["ledger"]
    assert ledger["root"] == os.path.join(kept.artifact_dir(ctx), "ledger")
    reader = _read_back(ledger["root"], ledger["series_id"])
    assert reader.head() == (ledger["seq"], ledger["hash"])


@pytest.mark.parametrize(
    ("guards", "expected"),
    [
        ([], "guards must be a mapping"),
        ({"size": {"uses": "nope"}}, "unknown kind 'nope'"),
        ({"size": {"uses": "limit", "params": {"measure": "quantity"}}}, "bound"),
        ({"size": {"uses": "limit", "bogus": 1}}, "unknown key(s) ['bogus']"),
    ],
    ids=["not-a-map", "unknown-kind", "guard-refuses-its-params", "site-default-deny"],
)
def test_development_replay_validates_guards_through_the_production_path(guards, expected):
    problems = DevelopmentReplay.validate_params(_shipped_replay_params(guards=guards))
    assert problems and any(expected in problem for problem in problems)


def test_development_replay_accepts_a_sound_guard_map():
    params = _shipped_replay_params(guards=_observational_guards(), keep_ledger=False)
    assert DevelopmentReplay.validate_params(params) == []


@pytest.mark.parametrize("value", [1, "true", None])
def test_development_replay_keep_ledger_must_be_a_bool(value):
    problems = DevelopmentReplay.validate_params(_shipped_replay_params(keep_ledger=value))
    assert any("keep_ledger must be a bool" in problem for problem in problems)


# --- ADR-0185: the scheduled (biweekly Friday) cash-flow policy --------------

_SCHEDULED = {
    "kind": "scheduled",
    "currency": "USD",
    "initial_capital_amount": "10000",
    "contribution_amount": "500",
    "interval_days": 14,
    "first_weekday": "friday",
    "local_time": "09:30",
    "timezone": "America/New_York",
    "holiday_rule": "next_trading_day",
}


def _scheduled(**overrides):
    return ScheduledCashFlowPolicy({**_SCHEDULED, **overrides})


def _weekdays(first, last, drop=()):
    days, day = [], first
    while day <= last:
        if day.weekday() < 5 and day not in drop:
            days.append(day)
        day += timedelta(days=1)
    return frozenset(days)


def _at_930(day):
    return _local_ms(day.year, day.month, day.day, 9, 30)


def _plan(policy, dates, start=None):
    """``(date, amount)`` of every flow the composer books over ``dates``."""
    first = min(dates)
    composer, instants = policy.composer_for(
        "series-s", start if start is not None else _at_930(first), dates
    )
    last = max(dates) + timedelta(days=1)
    due = composer.due(_utc(_at_930(first)), _utc(_local_ms(last.year, last.month, last.day, 0, 0)))
    rows = [(record["body"]["effective_at_ms"], Decimal(record["body"]["amount"])) for record in due]
    return [(_utc(ms).astimezone(_CF_TZ).date(), amount) for ms, amount in sorted(rows)], instants


def test_scheduled_first_contribution_is_the_first_friday_on_or_after_day_one_then_every_14_days():
    dates = _weekdays(date(2026, 1, 5), date(2026, 2, 6))  # Monday .. Friday
    plan, instants = _plan(_scheduled(), dates)
    assert plan == [
        (date(2026, 1, 5), Decimal("10000")),
        (date(2026, 1, 9), Decimal("500")),
        (date(2026, 1, 23), Decimal("500")),
        (date(2026, 2, 6), Decimal("500")),
    ]
    assert instants == tuple(_at_930(day) for day, _ in plan)


def test_scheduled_a_friday_first_day_books_seed_plus_contribution_at_once():
    dates = _weekdays(date(2026, 1, 9), date(2026, 1, 23))
    plan, _ = _plan(_scheduled(), dates)
    assert plan == [(date(2026, 1, 9), Decimal("10500")), (date(2026, 1, 23), Decimal("500"))]


def test_scheduled_a_holiday_friday_rolls_to_the_next_trading_day_at_0930():
    dates = _weekdays(date(2026, 1, 5), date(2026, 1, 30), drop={date(2026, 1, 23)})
    plan, instants = _plan(_scheduled(), dates)
    assert (date(2026, 1, 26), Decimal("500")) in plan
    assert all(day != date(2026, 1, 23) for day, _ in plan)
    assert _at_930(date(2026, 1, 26)) in instants


def test_scheduled_dated_overrides_skip_move_replace_add_and_withdraw():
    dates = _weekdays(date(2026, 1, 5), date(2026, 2, 20))
    policy = _scheduled(overrides=[
        {"kind": "skip", "date": "2026-01-09"},
        {"kind": "move", "date": "2026-01-23", "to": "2026-01-21"},
        {"kind": "replace", "date": "2026-02-06", "amount": "750"},
        {"kind": "one_off", "date": "2026-01-14", "amount": "125"},
        {"kind": "withdrawal", "date": "2026-02-11", "amount": "300"},
    ])
    plan, _ = _plan(policy, dates)
    assert plan == [
        (date(2026, 1, 5), Decimal("10000")),
        (date(2026, 1, 14), Decimal("125")),
        (date(2026, 1, 21), Decimal("500")),
        (date(2026, 2, 6), Decimal("750")),
        (date(2026, 2, 11), Decimal("-300")),
        (date(2026, 2, 20), Decimal("500")),
    ]


def test_scheduled_segments_keep_the_global_phase_across_a_63_day_release_boundary():
    everything = _weekdays(date(2026, 1, 5), date(2026, 4, 3))
    policy = _scheduled()
    boundary = date(2026, 3, 9)  # a Monday, 63 days after the first day
    second = frozenset(day for day in everything if day >= boundary)
    segment = policy.segment(Decimal("1234.5"), everything)
    plan, _ = _plan(segment, second)
    # The global phase funds Fri 2026-03-06, 03-20, 04-03; a re-anchored
    # phase would fund 03-13 and 03-27 instead.
    assert plan == [
        (date(2026, 3, 9), Decimal("1234.5")),
        (date(2026, 3, 20), Decimal("500")),
        (date(2026, 4, 3), Decimal("500")),
    ]


def test_scheduled_a_holiday_roll_crosses_into_the_next_segment_exactly_once():
    everything = _weekdays(date(2026, 1, 5), date(2026, 2, 6), drop={date(2026, 1, 23)})
    first = frozenset(day for day in everything if day < date(2026, 1, 26))
    second = frozenset(day for day in everything if day >= date(2026, 1, 26))
    policy = _scheduled()
    plan_one, _ = _plan(policy.segment(None, everything), first)
    plan_two, _ = _plan(policy.segment(Decimal("9000"), everything), second)
    assert plan_one == [(date(2026, 1, 5), Decimal("10000")), (date(2026, 1, 9), Decimal("500"))]
    assert plan_two == [(date(2026, 1, 26), Decimal("9500")), (date(2026, 2, 6), Decimal("500"))]


def test_scheduled_a_zero_carry_on_a_non_contribution_day_books_nothing():
    everything = _weekdays(date(2026, 1, 5), date(2026, 1, 30))
    second = frozenset(day for day in everything if day >= date(2026, 1, 12))
    plan, instants = _plan(_scheduled().segment(Decimal("0"), everything), second)
    assert plan == [(date(2026, 1, 23), Decimal("500"))]
    assert instants == (_at_930(date(2026, 1, 23)),)


def test_scheduled_a_tape_opening_after_0930_books_the_0930_seed_before_its_first_decision():
    first = date(2026, 1, 5)
    late = _at_930(first) + 60 * 60_000
    seen = []

    class _Spy:
        def decide(self, asof_ms, portfolio):
            seen.append((asof_ms, portfolio["cash"]))
            return []

    replay = EquityReplay(_policy(), _scheduled(), decider=_Spy())
    replay.run([_bar("AAA", late, 10.0, 10.1), _bar("AAA", late + 60_000, 10.1, 10.2)])
    assert [(row["asof_ms"], row["amount"]) for row in replay.cash_flows] == [(_at_930(first), 10000.0)]
    assert seen[0] == (late, 10000.0)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("kind", "weekly", "kind"),
        ("interval_days", 0, "interval_days"),
        ("interval_days", True, "interval_days"),
        ("first_weekday", "fri", "first_weekday"),
        ("local_time", "9:30am", "local_time"),
        ("holiday_rule", "skip", "holiday_rule"),
        ("contribution_amount", "-5", "contribution_amount"),
        ("overrides", [{"kind": "skip"}], "overrides"),
        ("overrides", [{"kind": "grow", "date": "2026-01-09"}], "overrides"),
        ("bogus", 1, "unknown"),
    ],
)
def test_scheduled_policy_refuses_a_malformed_knob(field, value, expected):
    problems = ScheduledCashFlowPolicy.validate_params({**_SCHEDULED, field: value})
    assert problems and any(expected in problem for problem in problems)


def test_the_loader_dispatches_on_kind_and_the_daily_file_is_untouched():
    assert type(CashFlowPolicy.from_path(CASH_FLOW_POLICY_PATH)) is CashFlowPolicy
    assert CashFlowPolicy.from_path(CASH_FLOW_POLICY_PATH).digest() == (
        "501e27afca2ecae6aeb7a3d66136001aafa4d52dab9c98409496d0c446f0c878"
    )
    loaded = CashFlowPolicy.from_path(os.path.join(CONFIGS, "cash-flow-policy-biweekly.json"))
    assert type(loaded) is ScheduledCashFlowPolicy
    assert loaded.initial_capital_amount == Decimal("10000")
    assert loaded.contribution_amount == Decimal("500")


def test_a_replay_books_the_scheduled_flows_and_names_their_rules():
    first, friday = date(2026, 1, 5), date(2026, 1, 9)
    bars = [
        _bar("AAA", _at_930(first), 10.0, 10.1),
        _bar("AAA", _at_930(first) + 60_000, 10.1, 10.2),
        _bar("AAA", _at_930(date(2026, 1, 6)), 10.0, 10.1),
        _bar("AAA", _at_930(friday), 11.0, 11.1),
    ]
    replay = _CapturingEquityReplay(_policy(), _scheduled())
    out = replay.run(bars, [])
    assert [(row["rule"], row["amount"]) for row in out["cash_flows"]] == [
        ("initial_capital", 10000.0), ("scheduled_contribution", 500.0),
    ]
    assert replay._cash_balance == Decimal("10500")
