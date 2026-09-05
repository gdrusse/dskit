"""P11 asset-local stopping and untouched confirmation tests."""

import json
from pathlib import Path
from types import SimpleNamespace


from dskit.pipeline.document import PipelineDocument, load_document

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


def test_p11_config_has_gate1_followed_directly_by_gate3():
    raw = json.loads((_root() / "configs" / "run-p11-modelability.json").read_text())
    assert list(raw["stages"]) == ["memory", "gate1", "gate3_walks", "gate3"]
    assert "gate2" not in raw["stages"]
    assert raw["stages"]["gate3_walks"]["params"]["seeds"] == list(range(19))
    assert raw["stages"]["gate3"]["params"]["seeds"] == list(range(19))
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
    assert list(document.pipeline) == [
        "universe",
        "features",
        "asset_features",
        "reference_tape",
        "scan",
    ]
    filt = document.pipeline["asset_features"]
    assert filt.inputs == {"records": "$features.records"}
    assert filt.params["where"] == [{"field": "symbol", "op": "==", "value": "JPM"}]
    scan = document.pipeline["scan"]
    assert scan.inputs["records"] == "$asset_features.records"
    assert scan.inputs["bars"] == "$reference_tape.records"
    assert scan.params["fit_symbols"] == ["JPM"]
    assert scan.params["score_symbols"] == ["JPM"]
    assert document.stages is None


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
        lambda _ctx, doc, _tag, **_kw: (
            calls.append(doc.horizon) or f"walk-{doc.horizon}"
        ),
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


def test_gate3_refits_only_gate1_survivors_and_requires_all_nulls_to_lose(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(p11, "_ASSETS", ["A", "B"])
    walks = p11.Gate3WalksStage("gate3_walks", {"seeds": list(range(19))})
    documents = []
    monkeypatch.setattr(
        p11,
        "_derived_document",
        lambda _ctx, asset, horizon, **kwargs: (
            documents.append((asset, horizon, kwargs))
            or SimpleNamespace(
                asset=asset, horizon=horizon, seed=kwargs["scramble_seed"]
            )
        ),
    )
    monkeypatch.setattr(
        p11.p10,
        "_run_bounded_walk",
        lambda _ctx, doc, _tag, **_kw: f"walk-{doc.asset}-{doc.horizon}-{doc.seed}",
    )
    gate1 = [
        {"asset": "A", "gate1_h": 2, "gate1_passes": True},
        {"asset": "B", "gate1_h": None, "gate1_passes": False},
    ]
    out = walks.run(SimpleNamespace(), {"gate1": gate1})
    assert len(documents) == 19
    assert {asset for asset, _horizon, _kwargs in documents} == {"A"}
    assert out["survivors"] == ["A"]

    result = p11.Gate3ResultStage(
        "gate3", {"assets": ["A", "B"], "seeds": list(range(19)), "alpha": 0.05}
    )
    monkeypatch.setattr(
        p11,
        "_score_one",
        lambda summary, _asset, _horizon, _alpha: {
            "r2oos": 0.01 if summary.startswith("walk") else 0.0,
            "t_pool": 0.0,
        },
    )
    cells = [{"asset": "A", "horizon": 2, "skill": {"r2oos": 0.02}}]
    monkeypatch.setattr(p11, "tier2_verdict", lambda *_args: {"passes": True})
    final = result.run(
        SimpleNamespace(), {"gate1": gate1, "gate1_cells": cells, "walks": out["walks"]}
    )
    assert final["rows"][0]["gate3_status"] == "pass"
    assert final["rows"][1]["gate3_status"] == "not_reached"


def test_derived_walk_filters_the_tape_to_the_asset_and_its_reference(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        p11.p10,
        "_feature_cache_info",
        lambda _ctx: ("./cache", "/cache", "a" * 64),
    )
    document = p11._derived_document(_context(tmp_path), "JPM", 3)
    assert list(document.pipeline) == [
        "universe",
        "features",
        "asset_features",
        "reference_tape",
        "scan",
    ]
    tape = document.pipeline["reference_tape"]
    assert tape.uses == "filter"
    assert tape.inputs == {"records": "$features.tape"}
    assert tape.params["where"] == [
        {"field": "symbol", "op": "in", "value": ["JPM", "SPY"]}
    ]
    assert document.pipeline["scan"].inputs["bars"] == "$reference_tape.records"


def test_derived_walk_keeps_only_spy_when_spy_is_the_asset(tmp_path, monkeypatch):
    monkeypatch.setattr(
        p11.p10,
        "_feature_cache_info",
        lambda _ctx: ("./cache", "/cache", "a" * 64),
    )
    document = p11._derived_document(_context(tmp_path), "SPY", 3)
    assert document.pipeline["reference_tape"].params["where"] == [
        {"field": "symbol", "op": "in", "value": ["SPY"]}
    ]


def _preflight_harness(tmp_path, monkeypatch, peak):
    monkeypatch.setattr(p11, "_ASSETS", ["A"])
    monkeypatch.setattr(
        p11.p10, "_feature_cache_info", lambda _ctx: ("./cache", "/cache", "a" * 64)
    )
    monkeypatch.setattr(p11, "_largest_asset", lambda _path, _assets: ("A", 10))
    tags = []
    monkeypatch.setattr(
        p11,
        "_derived_document",
        lambda _ctx, asset, horizon, **kwargs: (
            tags.append(kwargs["tag"]) or SimpleNamespace(asset=asset, horizon=horizon)
        ),
    )
    measured = []
    monkeypatch.setattr(
        p11.p10,
        "_measure_walk",
        lambda _ctx, doc, tag: (measured.append((doc.asset, tag)) or ("/summary", peak)),
    )
    monkeypatch.setattr(p11, "_score_one", lambda *_a: {"r2oos": 0.0})
    stage = p11.MemoryPreflightStage(
        "memory", {"assets": ["A"], "memory_limit_bytes": p11._MEMORY_LIMIT}
    )
    ctx = SimpleNamespace(document=SimpleNamespace(hash="f" * 64))
    return stage, ctx, tags, measured


def test_the_preflight_measures_one_fresh_walk_per_study_identity(
    tmp_path, monkeypatch
):
    # ADR-0093: the reading comes from measure_one, which needs a fresh
    # spawn, so the derived preflight walk is named for the staged
    # document's identity — a revised study measures again instead of
    # tripping over the previous study's finished walk.
    stage, ctx, tags, measured = _preflight_harness(tmp_path, monkeypatch, peak=5)
    out = stage.run(ctx, {})
    assert measured == [("A", "p11-memory-a")]
    assert tags == ["preflight-" + "f" * 8]
    assert out["peak_rss_bytes"] == 5
    assert out["summary_dir"] == "/summary"
    assert out["passed"] is True
    assert out["limit_bytes"] == p11._MEMORY_LIMIT


def test_the_preflight_refuses_a_peak_at_the_limit(tmp_path, monkeypatch):
    stage, ctx, _tags, _measured = _preflight_harness(
        tmp_path, monkeypatch, peak=p11._MEMORY_LIMIT
    )
    try:
        stage.run(ctx, {})
    except MemoryError as error:
        assert "strictly below" in str(error)
    else:  # pragma: no cover - the assertion below reports it
        raise AssertionError("a peak at the limit passed")


def test_the_memory_envelope_is_one_value_in_both_modules():
    # p10's constant is what a fold actually gets; p11's is what P11's
    # preflight validates against. Two copies, so pin the agreement.
    assert p11._MEMORY_LIMIT == p11.p10._MEMORY_LIMIT


def test_the_worker_knob_is_never_graded_into_the_document_identity():
    # The real claim: no stage declares it, so no width can move the
    # hash that names the run directory and keys the stored artifacts.
    for name in ("run-p10-modelability.json", "run-p11-modelability.json"):
        raw = json.loads((_root() / "configs" / name).read_text())
        for stage in raw["stages"].values():
            assert "workers" not in stage.get("params", {})
    for stage_class in (
        p11.Gate1Stage,
        p11.Gate3WalksStage,
        p11.p10.Gate1WalksStage,
        p11.p10.Gate3WalksStage,
    ):
        assert "workers" not in stage_class._PARAMS
    assert (
        p11.Gate3WalksStage.validate_params({"seeds": list(range(19)), "workers": 4})
        != []
    )


def test_the_reference_tape_follows_the_configs_declared_residual(
    tmp_path, monkeypatch
):
    # The kept tape must be derived from label_residual, not from a
    # second copy of "SPY": editing the config would otherwise drop the
    # reference and fail inside the fold subprocess, and only for the
    # assets that are not themselves the residual.
    monkeypatch.setattr(
        p11.p10, "_feature_cache_info", lambda _ctx: ("./cache", "/cache", "a" * 64)
    )
    ctx = _context(tmp_path)
    obj = ctx.document.to_obj()
    obj["pipeline"]["scan"]["params"]["label_residual"] = "QQQ"
    ctx.document = PipelineDocument.from_obj(obj)
    document = p11._derived_document(ctx, "JPM", 3)
    assert document.pipeline["reference_tape"].params["where"] == [
        {"field": "symbol", "op": "in", "value": ["JPM", "QQQ"]}
    ]


def test_the_reference_tape_keeps_one_symbol_when_no_residual_is_declared(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        p11.p10, "_feature_cache_info", lambda _ctx: ("./cache", "/cache", "a" * 64)
    )
    ctx = _context(tmp_path)
    obj = ctx.document.to_obj()
    obj["pipeline"]["scan"]["params"].pop("label_residual")
    ctx.document = PipelineDocument.from_obj(obj)
    document = p11._derived_document(ctx, "JPM", 3)
    assert document.pipeline["reference_tape"].params["where"] == [
        {"field": "symbol", "op": "in", "value": ["JPM"]}
    ]
