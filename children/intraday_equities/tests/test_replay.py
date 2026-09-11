"""Gate 5 replay policies — synthetic only (ADR-0119 proposed).

No market data, no sealed P16 artifact load, no real confirmed caps.
Every fill-model value is read from config; the tests below fail if the
adapter invents a default the document did not name.
"""

from __future__ import annotations

import ast
import copy
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from dskit.pipeline.base import config_hash
from dskit.pipeline.document import load_document
from dskit.pipeline.node import ConfigError

from intraday_equities.nodes_capital import SchwabCostModel
from intraday_equities.replay import (
    DevelopmentReplay,
    FillPolicy,
    ReplayAdapter,
)

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS = os.path.join(CHILD_ROOT, "configs")
FILL_POLICY_PATH = os.path.join(CONFIGS, "fill-policy.json")
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
        {"symbol": "AAA", "asof_ms": 2_000, "reason": "halted"}
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
