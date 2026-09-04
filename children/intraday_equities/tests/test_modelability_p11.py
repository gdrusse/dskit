"""P11 asset-local stopping and untouched confirmation tests."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from dskit.pipeline.document import load_document

from intraday_equities import modelability_p11 as p11


def _root():
    return Path(__file__).parents[1]


def _context(tmp_path):
    source = _root() / "configs" / "run-p11-modelability.json"
    return SimpleNamespace(
        document=load_document(str(source)),
        source_path=str(source),
        run_dir=str(tmp_path / "stage"),
        asof="2026-02-28",
    )


def test_p11_config_has_only_memory_and_the_first_two_gates():
    raw = json.loads(
        (_root() / "configs" / "run-p11-modelability.json").read_text()
    )
    assert list(raw["stages"]) == ["memory", "gate1", "gate2"]
    assert "gate3" not in json.dumps(raw).lower()
    assert raw["pipeline"]["scan"]["params"]["fit_symbols"] == p11._ASSETS
    assert len(p11._ASSETS) == 25
    assert "META" not in p11._ASSETS
    assert "GROUP" not in p11._ASSETS
    assert raw["stages"]["gate1"]["params"]["horizons"] == p11._HORIZONS
    sources = {
        node["params"]["source"]
        for node in raw["pipeline"].values()
        if node.get("uses") == "intraday_equities-bars"
    }
    assert sources == {
        "alpaca-sip-split",
        "alpaca-sip-split-b",
        "alpaca-sip-split-c",
    }
    for name in ("run-p10-modelability.json", "run-p11-modelability.json"):
        pinned = json.loads((_root() / "configs" / name).read_text())
        sink = pinned["tracking"]["sinks"]
        assert sink[0]["params"]["experiment"] == "intraday_equities"


def test_derived_walk_filters_features_but_keeps_reference_tape(tmp_path, monkeypatch):
    monkeypatch.setattr(
        p11.p10,
        "_feature_cache_info",
        lambda _ctx: ("./cache", "/cache", "a" * 64),
    )
    document = p11._derived_document(_context(tmp_path), "JPM", 3)
    assert list(document.pipeline) == ["universe", "features", "asset_features", "scan"]
    filt = document.pipeline["asset_features"]
    assert filt.inputs == {"records": "$features.records"}
    assert filt.params["where"] == [{"field": "symbol", "op": "==", "value": "JPM"}]
    scan = document.pipeline["scan"]
    assert scan.inputs["records"] == "$asset_features.records"
    assert scan.inputs["bars"] == "$features.tape"
    assert scan.params["fit_symbols"] == ["JPM"]
    assert scan.params["score_symbols"] == ["JPM"]
    assert document.stages is None


def test_confirmation_geometry_is_untouched_and_ends_at_the_cut(tmp_path, monkeypatch):
    monkeypatch.setattr(
        p11.p10,
        "_feature_cache_info",
        lambda _ctx: ("./cache", "/cache", "a" * 64),
    )
    document = p11._derived_document(
        _context(tmp_path), "SPY", 2, confirmation=True, tag="gate2"
    )
    walk = document.walkforward
    assert walk.first == "2025-12-02"
    assert walk.count == 1
    assert walk.val_days == 89
    assert walk.embargo_days == 5
    assert walk.train_days == 730


def test_gate1_stops_on_first_failure_and_never_runs_or_registers_later(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(p11, "_ASSETS", ["A"])
    monkeypatch.setattr(p11, "_HORIZONS", [1, 2, 3])
    stage = p11.Gate1Stage(
        "gate1",
        {
            "assets": ["A"],
            "horizons": [1, 2, 3],
            "attempt_registry": "attempts.jsonl",
            "alpha": 0.05,
        },
    )
    calls = []
    registered = []
    monkeypatch.setattr(p11.p10, "_child_root", lambda _ctx: str(tmp_path))
    monkeypatch.setattr(
        p11,
        "_derived_document",
        lambda _ctx, asset, horizon: SimpleNamespace(asset=asset, horizon=horizon),
    )
    monkeypatch.setattr(
        p11.p10,
        "_run_bounded_walk",
        lambda _ctx, doc, _tag: calls.append(doc.horizon) or f"walk-{doc.horizon}",
    )
    monkeypatch.setattr(
        p11,
        "_score_one",
        lambda _summary, _asset, horizon, _alpha: {
            "passes": horizon == 1,
            "t_pool": 2.0,
            "t_fold": 2.0,
            "r2oos": 0.01,
            "n_folds": 20,
        },
    )

    class Registry:
        def __init__(self, _path):
            pass

        def record(self, key, **_fields):
            registered.append(key["horizon"])
            return f"cell-{key['horizon']}"

    monkeypatch.setattr(p11, "AttemptRegistry", Registry)
    out = stage.run(SimpleNamespace(), {"preflight": True})
    assert calls == [1, 2]
    assert registered == [1, 2]
    assert out["rows"] == [
        {
            "asset": "A",
            "gate1_h": 1,
            "gate1_passes": True,
            "first_failed_h": 2,
            "attempted_horizons": [1, 2],
            "unrun_horizons": [3],
        }
    ]


def test_gate2_runs_only_survivors_and_fixes_all_family_allocations(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(p11, "_ASSETS", ["A", "B"])
    monkeypatch.setattr(p11, "_FAMILY", "confirm")
    stage = p11.Gate2Stage(
        "gate2",
        {
            "assets": ["A", "B"],
            "attempt_registry": "attempts.jsonl",
            "family": "confirm",
            "alpha": 0.05,
        },
    )
    calls = []
    monkeypatch.setattr(p11.p10, "_child_root", lambda _ctx: str(tmp_path))
    monkeypatch.setattr(
        p11,
        "_derived_document",
        lambda _ctx, asset, horizon, **_kwargs: SimpleNamespace(
            asset=asset, horizon=horizon
        ),
    )
    monkeypatch.setattr(
        p11.p10,
        "_run_bounded_walk",
        lambda _ctx, doc, _tag: calls.append((doc.asset, doc.horizon)) or "walk-a",
    )
    monkeypatch.setattr(
        p11,
        "_score_one",
        lambda *_args: {
            "t_pool": 3.0,
            "r2oos": 0.01,
            "n_folds": 1,
            "n_rows": 100,
        },
    )
    gate1 = [
        {"asset": "A", "gate1_h": 2, "gate1_passes": True},
        {"asset": "B", "gate1_h": None, "gate1_passes": False},
    ]
    out = stage.run(SimpleNamespace(), {"gate1": gate1})
    assert calls == [("A", 2)]
    assert out["ledger_header"]["keys"] == ["A", "B"]
    assert out["ledger_header"]["allocation"] == pytest.approx(0.025)
    assert [row["key"] for row in out["ledger_results"]] == ["A"]
    assert out["rows"][1]["gate2_status"] == "not_reached"
    assert out["rows"][1]["not_reached_reason"] == "gate1_failed_at_h1"
