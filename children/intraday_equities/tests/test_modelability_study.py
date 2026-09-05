"""The asset-local modelability study over a document-declared cohort (ADR-0094).

What is pinned here, and why: the cohort is the document's ``fit_symbols``
and nothing else names it; every derived walk fits and scores exactly one
asset, and the only other fit in the study is the reference symbol alone
inside a cache build — there is no pooled fit anywhere; each asset belongs
to exactly one group cache; the memory stage measures the first cache it
builds (ADR-0093 allows one reading per process) and says what it
measured; Gate 1 stops at the first failure and Gate 3 is ADR-0092's
fail-fast audit, keyed per asset. The P12 document mirrors P11's geometry
key for key.
"""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from dskit.pipeline.document import PipelineDocument, load_document

import intraday_equities  # noqa: F401 — import = registration (ADR-0021)
from intraday_equities import modelability_study as study

CHILD = Path(__file__).parents[1]
CONFIGS = CHILD / "configs"
P12 = CONFIGS / "run-p12-modelability.json"
GEOMETRY_KEYS = (
    "holidays",
    "lookback",
    "max_gap_minutes",
    "period_ms",
    "offset_ms",
    "price_field",
    "session",
    "scales",
    "horizon",
)


def _raw(name):
    return json.loads((CONFIGS / name).read_text(encoding="utf-8"))


def _digest(name):
    import hashlib

    return hashlib.sha256((CONFIGS / name).read_bytes()).hexdigest()


def _ctx(tmp_path, document=None):
    return SimpleNamespace(
        document=document or load_document(str(P12)),
        source_path=str(P12),
        run_dir=str(tmp_path / "stage"),
        asof="2026-02-28",
    )


# -- the shipped document ------------------------------------------------------


def test_p12_mirrors_p11_geometry_and_declares_the_forty_as_its_cohort():
    p12 = _raw("run-p12-modelability.json")
    p11 = _raw("run-p11-modelability.json")
    scan12 = {k: v for k, v in p12["pipeline"]["scan"]["params"].items() if k != "fit_symbols"}
    scan11 = {k: v for k, v in p11["pipeline"]["scan"]["params"].items() if k != "fit_symbols"}
    assert scan12 == scan11
    f12 = {k: v for k, v in p12["pipeline"]["features"]["params"].items() if k != "cache_dir"}
    f11 = {k: v for k, v in p11["pipeline"]["features"]["params"].items() if k != "cache_dir"}
    assert f12 == f11
    assert p12["walkforward"] == p11["walkforward"]
    assert p12["pipeline"]["features"]["params"]["cache_dir"] != p11["pipeline"]["features"]["params"]["cache_dir"]
    d = _raw("source-alpaca-split-d-backfill.json")["symbols"]
    e = _raw("source-alpaca-split-e-backfill.json")["symbols"]
    cohort = p12["pipeline"]["scan"]["params"]["fit_symbols"]
    assert cohort == d + e
    assert len(cohort) == 40
    assert "SPY" not in cohort and "META" not in cohort and "GROUP" not in cohort
    assert list(p12["stages"]) == ["memory", "gate1", "gate3_walks", "gate3"]
    for key, stage in p12["stages"].items():
        assert "assets" not in stage.get("params", {}), key
    alphas = {stage["params"]["alpha"] for stage in p12["stages"].values() if "alpha" in stage["params"]}
    assert len(alphas) == 1
    assert p12["stages"]["gate3_walks"]["params"]["seeds"] == list(range(19))
    assert p12["stages"]["gate3"]["params"]["seeds"] == list(range(19))
    assert p12["stages"]["gate1"]["params"]["horizons"] == [1, 2, 3, 5, 10, 20, 30, 60]


def test_the_three_universes_share_p10s_geometry_and_partition_the_cohort():
    p10 = _raw("universe-p10.json")
    union = _raw("universe-p12.json")
    groups = {"d": _raw("universe-p12-d.json"), "e": _raw("universe-p12-e.json")}
    for name, spec in {"union": union, **groups}.items():
        for key in GEOMETRY_KEYS:
            assert spec[key] == p10[key], (name, key)
        assert spec["reference"] == ["SPY"]
        assert set(spec["symbols"]) == set(spec["tradable"]) | {"SPY"}
        # ADR-0094 §3: no industry block, so no constant one-hot columns.
        assert "industry" not in spec, name
    sources = {
        "d": _raw("source-alpaca-split-d-backfill.json")["symbols"],
        "e": _raw("source-alpaca-split-e-backfill.json")["symbols"],
    }
    for key, spec in groups.items():
        assert spec["tradable"] == sources[key], key
    assert union["tradable"] == groups["d"]["tradable"] + groups["e"]["tradable"]
    assert union["tradable"] == _raw("run-p12-modelability.json")["pipeline"]["scan"]["params"]["fit_symbols"]


def _feature_names(universe, features):
    """The columns SessionFeatureRows emits for one universe and node params."""
    from intraday_equities.nodes import _emit_feature_names

    industries = tuple(sorted(set((universe.get("industry") or {}).values())))
    return list(
        _emit_feature_names(
            features["lookback"],
            universe["scales"],
            universe["reference"],
            industries,
            features.get("momentum_horizons", ()),
            features.get("feature_blocks", ()),
        )
    )


def test_the_group_caches_share_one_design_matrix_p11s_less_its_dead_one_hots():
    p11 = _raw("run-p11-modelability.json")["pipeline"]["features"]["params"]
    p12 = _raw("run-p12-modelability.json")["pipeline"]["features"]["params"]
    d, e, union = (
        _feature_names(_raw(f"universe-p12{suffix}.json"), p12)
        for suffix in ("-d", "-e", "")
    )
    assert d == e == union
    p10 = _raw("universe-p10.json")
    p11_names = _feature_names(p10, p11)
    assert d == [name for name in p11_names if not name.startswith("industry_")]
    assert len(p11_names) - len(d) == len(set(p10["industry"].values()))
    assert not any(name.startswith("industry_") for name in d)


def test_gate1s_data_cut_is_the_cut_every_source_declares():
    p12 = _raw("run-p12-modelability.json")
    cut = p12["stages"]["gate1"]["params"]["data_cut"]
    seen = 0
    for node in p12["pipeline"].values():
        if node.get("uses") != "intraday_equities-bars":
            continue
        source = node["params"]["source"]
        config = _raw(source.replace("alpaca-sip", "source-alpaca", 1) + "-backfill.json")
        assert config["end"].startswith(cut), source
        seen += 1
    assert seen == 3


def test_the_memory_groups_name_the_group_universes_and_their_sources():
    p12 = _raw("run-p12-modelability.json")
    groups = p12["stages"]["memory"]["params"]["groups"]
    assert groups == {
        "d": {"universe": "configs/universe-p12-d.json", "sources": ["source_reference", "source_d"]},
        "e": {"universe": "configs/universe-p12-e.json", "sources": ["source_reference", "source_e"]},
    }
    for group in groups.values():
        for key in group["sources"]:
            assert p12["pipeline"][key]["uses"] == "intraday_equities-bars", key
    assert p12["pipeline"]["source_reference"]["params"]["source"] == "alpaca-sip-split"
    assert p12["pipeline"]["source_d"]["params"]["source"] == "alpaca-sip-split-d"
    assert p12["pipeline"]["source_e"]["params"]["source"] == "alpaca-sip-split-e"
    for key in ("gate1", "gate3_walks"):
        assert p12["stages"][key]["inputs"]["caches"] == "$memory.groups", key


def test_the_shipped_document_plans_offline():
    from dskit.pipeline.stages import plan_stages

    plan = plan_stages(load_document(str(P12)))
    assert plan.order == ("memory", "gate1", "gate3_walks", "gate3")
    assert plan.document.stages["gate3"].inputs["draws"] == "$gate3_walks.draws"
    assert plan.classes["gate1"] is study.Gate1Stage


# -- the estimand: one asset, one fit, no pooled fit anywhere ----------------


def test_every_declared_asset_gets_its_own_fit_and_no_pooled_fit_exists(tmp_path):
    ctx = _ctx(tmp_path)
    cohort = study._document_cohort(ctx.document)
    caches = {
        "d": {"universe": "configs/universe-p12-d.json", "cache": "./c/d", "manifest_sha256": "a" * 64, "symbols": _raw("universe-p12-d.json")["symbols"]},
        "e": {"universe": "configs/universe-p12-e.json", "cache": "./c/e", "manifest_sha256": "b" * 64, "symbols": _raw("universe-p12-e.json")["symbols"]},
    }
    placement = study._place(cohort, caches)
    assert sorted(placement) == sorted(cohort)
    fits = []
    for asset in cohort:
        for horizon in (1, 60):
            for seed in (None, 3):
                doc = study.asset_walk_document(
                    ctx.document, ctx.document.name, asset, horizon, caches[placement[asset]], tag="gate1", scramble_seed=seed
                )
                scan = doc.pipeline["scan"].params
                assert scan["fit_symbols"] == [asset] == scan["score_symbols"]
                assert doc.pipeline["universe"].params["path"] == caches[placement[asset]]["universe"]
                assert doc.pipeline["features"].params == {"path": caches[placement[asset]]["cache"], "manifest_sha256": caches[placement[asset]]["manifest_sha256"]}
                fits.append(scan["fit_symbols"])
    builds = [
        study.cache_build_document(ctx.document, group, spec["sources"], spec["universe"], f"./c/{group}", "2025-08-15")
        for group, spec in ctx.document.stages["memory"].params["groups"].items()
    ]
    for build in builds:
        scan = build.pipeline["scan"].params
        assert scan["fit_symbols"] == ["SPY"] == scan["score_symbols"]
        fits.append(scan["fit_symbols"])
    assert len(fits) == 40 * 2 * 2 + 2
    assert max(len(f) for f in fits) == 1, "a pooled fit exists"


def test_the_cohort_is_the_documents_fit_symbols_and_nothing_else():
    document = load_document(str(P12))
    assert study._document_cohort(document) == document.pipeline["scan"].params["fit_symbols"]
    obj = document.to_obj()
    obj["pipeline"]["scan"]["params"].pop("fit_symbols")
    with pytest.raises(ValueError, match="fit_symbols"):
        study._document_cohort(PipelineDocument.from_obj(obj))


def test_placement_refuses_an_asset_in_no_group_or_in_two():
    caches = {
        "d": {"symbols": ["A", "B", "SPY"]},
        "e": {"symbols": ["C", "SPY"]},
    }
    assert study._place(["A", "C", "B"], caches) == {"A": "d", "C": "e", "B": "d"}
    with pytest.raises(ValueError, match="no group"):
        study._place(["A", "Z"], caches)
    with pytest.raises(ValueError, match="two groups"):
        study._place(["A"], {**caches, "f": {"symbols": ["A"]}})
    with pytest.raises(ValueError, match="reference"):
        study._place(["SPY"], caches)


# -- the derived documents -----------------------------------------------------


def test_asset_walk_document_is_p11s_shape_over_the_group_cache(tmp_path):
    ctx = _ctx(tmp_path)
    cache = {"universe": "configs/universe-p12-e.json", "cache": "./pipeline_cache/x/e", "manifest_sha256": "c" * 64, "symbols": ["MSTR", "SPY"]}
    doc = study.asset_walk_document(ctx.document, "p12-40-asset-modelability", "MSTR", 5, cache, tag="gate3-seed03", scramble_seed=3)
    assert list(doc.pipeline) == ["universe", "features", "asset_features", "reference_tape", "scan"]
    assert doc.name == "p12-40-asset-modelability-gate3-seed03-mstr-h05"
    assert doc.pipeline["asset_features"].params["where"] == [{"field": "symbol", "op": "==", "value": "MSTR"}]
    assert doc.pipeline["reference_tape"].params["where"] == [{"field": "symbol", "op": "in", "value": ["MSTR", "SPY"]}]
    scan = doc.pipeline["scan"].params
    assert (scan["lead_start"], scan["lead_step"], scan["lead_stop"]) == (5, 5, 5)
    assert scan["label_scramble_seed"] == 3
    assert scan["label_residual"] == "SPY"
    assert doc.stages is None
    assert doc.walkforward == ctx.document.walkforward
    plain = study.asset_walk_document(ctx.document, "s", "MSTR", 5, cache, tag="gate1")
    assert "label_scramble_seed" not in plain.pipeline["scan"].params
    with pytest.raises(ValueError, match="scramble seed"):
        study.asset_walk_document(ctx.document, "s", "MSTR", 5, cache, tag="x", scramble_seed=True)


def test_cache_build_document_reads_only_the_groups_sources_and_fits_the_reference(tmp_path):
    ctx = _ctx(tmp_path)
    doc = study.cache_build_document(ctx.document, "d", ["source_reference", "source_d"], "configs/universe-p12-d.json", "./pipeline_cache/p12-features-f32-v5/d", "2025-08-15")
    assert list(doc.pipeline) == ["universe", "source_reference", "source_d", "pooled", "features", "reference_features", "reference_tape", "scan"]
    assert doc.name == "p12-40-asset-modelability-cache-d"
    assert doc.pipeline["universe"].params["path"] == "configs/universe-p12-d.json"
    for key in ("source_reference", "source_d"):
        assert doc.pipeline[key].params["universe"] == "configs/universe-p12-d.json"
        assert doc.pipeline[key].params["source"] == ctx.document.pipeline[key].params["source"]
    assert doc.pipeline["pooled"].inputs == {"reference": "$source_reference.records", "d": "$source_d.records"}
    assert doc.pipeline["features"].params["cache_dir"] == "./pipeline_cache/p12-features-f32-v5/d"
    assert doc.pipeline["features"].inputs["records"] == "$pooled.merged"
    assert doc.pipeline["reference_features"].params["where"] == [{"field": "symbol", "op": "==", "value": "SPY"}]
    assert doc.pipeline["reference_tape"].params["where"] == [{"field": "symbol", "op": "in", "value": ["SPY"]}]
    scan = doc.pipeline["scan"].params
    assert scan["fit_symbols"] == ["SPY"] == scan["score_symbols"]
    assert scan["lead_start"] == scan["lead_stop"] == ctx.document.pipeline["scan"].params["lead_start"]
    assert (doc.walkforward.first, doc.walkforward.count) == ("2025-08-15", 1)
    assert doc.stages is None


def test_cache_build_document_refuses_a_source_that_is_not_a_bars_node_or_a_document_without_a_reference(tmp_path):
    ctx = _ctx(tmp_path)
    with pytest.raises(ValueError, match="bars"):
        study.cache_build_document(ctx.document, "d", ["universe"], "configs/universe-p12-d.json", "./c/d", "2025-08-15")
    with pytest.raises(ValueError, match="unknown"):
        study.cache_build_document(ctx.document, "d", ["source_zzz"], "configs/universe-p12-d.json", "./c/d", "2025-08-15")
    obj = ctx.document.to_obj()
    obj["pipeline"]["scan"]["params"].pop("label_residual")
    with pytest.raises(ValueError, match="label_residual"):
        study.cache_build_document(PipelineDocument.from_obj(obj), "d", ["source_reference", "source_d"], "configs/universe-p12-d.json", "./c/d", "2025-08-15")


def test_cache_build_document_refuses_two_sources_that_name_one_pooled_input(tmp_path):
    # The pooled input name is the source key less its "source_" prefix,
    # so two distinct bars nodes can collapse onto one input and silently
    # drop a whole cohort from the cache the group builds.
    ctx = _ctx(tmp_path)
    obj = ctx.document.to_obj()
    obj["pipeline"]["d"] = json.loads(json.dumps(obj["pipeline"]["source_d"]))
    document = PipelineDocument.from_obj(obj)
    with pytest.raises(ValueError, match="pooled input"):
        study.cache_build_document(
            document, "d", ["source_d", "d"],
            "configs/universe-p12-d.json", "./c/d", "2025-08-15",
        )


# -- the scorer -----------------------------------------------------------------


def test_score_one_refuses_a_walk_that_scored_anything_but_the_one_asset_row(monkeypatch):
    row = {"series": "A", "lead": 2, "r2oos": 0.01}
    scored = {"exact": True, "rows": [row]}
    monkeypatch.setattr(
        study,
        "score_walk",
        lambda summary, alpha, group: {"exact": scored["exact"], "rows": list(scored["rows"])},
    )
    assert study._score_one("/s", "A", 2, 0.05) == row
    # A GROUP row beside the asset row: the asset filter still finds one.
    scored["rows"] = [row, {"series": "GROUP", "lead": 2, "r2oos": 0.0}]
    with pytest.raises(ValueError, match="one exact row"):
        study._score_one("/s", "A", 2, 0.05)
    scored["rows"] = [row, dict(row)]
    with pytest.raises(ValueError, match="one exact row"):
        study._score_one("/s", "A", 2, 0.05)
    scored["rows"] = [row]
    scored["exact"] = False
    with pytest.raises(ValueError, match="one exact row"):
        study._score_one("/s", "A", 2, 0.05)


# -- the memory stage -----------------------------------------------------------


def _memory_stage():
    return study.MemoryPreflightStage("memory", _raw("run-p12-modelability.json")["stages"]["memory"]["params"])


def test_memory_params_are_default_deny_and_shaped():
    ok = _raw("run-p12-modelability.json")["stages"]["memory"]["params"]
    assert study.MemoryPreflightStage.validate_params(ok) == []
    assert study.MemoryPreflightStage.validate_params({**ok, "workers": 2}) != []
    assert study.MemoryPreflightStage.validate_params({**ok, "memory_limit_bytes": 0}) != []
    assert study.MemoryPreflightStage.validate_params({**ok, "groups": {}}) != []
    assert study.MemoryPreflightStage.validate_params({**ok, "groups": {"d": {"universe": "x"}}}) != []
    assert study.MemoryPreflightStage.validate_params({**ok, "groups": {"d": {"universe": "x", "sources": []}}}) != []
    assert study.MemoryPreflightStage.validate_params({**ok, "groups": {"bad key": ok["groups"]["d"]}}) != []


def _memory_harness(tmp_path, monkeypatch, present):
    """Fake the seam and the cache verifier; record what the stage does."""
    ctx = _ctx(tmp_path)
    log = []
    universes = {"d": _raw("universe-p12-d.json"), "e": _raw("universe-p12-e.json")}

    def verified(path, group_universe, features_params):
        group = os.path.basename(path).split("-")[0]
        log.append(("verify", group))
        if group in present:
            return {"manifest_sha256": group[0] * 64, "symbols": universes[group]["symbols"]}
        return None

    def measure(_ctx, document, tag):
        log.append(("measure", tag))
        present.add(document.name.rsplit("-", 1)[-1])
        return f"/summary/{tag}", 7_000_000_000

    def run(_ctx, document, tag):
        log.append(("run", tag))
        present.add(document.name.rsplit("-", 1)[-1])
        return f"/summary/{tag}"

    monkeypatch.setattr(study, "_verified_cache", verified)
    monkeypatch.setattr(study.p10, "_measure_walk", measure)
    monkeypatch.setattr(study.p10, "_run_bounded_walk", run)
    monkeypatch.setattr(study, "_largest_asset", lambda _path, assets: (assets[0], 99))
    monkeypatch.setattr(study, "_score_one", lambda *_a: {"r2oos": 0.0})
    return ctx, log


def test_memory_builds_each_absent_group_and_measures_only_the_first(tmp_path, monkeypatch):
    ctx, log = _memory_harness(tmp_path, monkeypatch, present=set())
    out = _memory_stage().run(ctx, {})
    name = ctx.document.name
    assert [entry for entry in log if entry[0] != "verify"] == [
        ("measure", f"{name}-cache-d"),
        ("run", f"{name}-cache-e"),
    ]
    assert out["measured"] == {"kind": "cache_build", "name": "d", "summary_dir": f"/summary/{name}-cache-d", "peak_rss_bytes": 7_000_000_000}
    assert out["passed"] is True
    assert out["limit_bytes"] == 18253611008
    digest = _digest("universe-p12-d.json")
    assert out["groups"]["d"] == {
        "universe": "configs/universe-p12-d.json",
        "universe_sha256": digest,
        "cache": f"./pipeline_cache/p12-features-f32-v5/d-{digest[:8]}",
        "manifest_sha256": "d" * 64,
        "symbols": _raw("universe-p12-d.json")["symbols"],
    }
    assert out["groups"]["e"]["manifest_sha256"] == "e" * 64
    assert out["groups"]["e"]["cache"].endswith(
        "/e-" + _digest("universe-p12-e.json")[:8]
    )


def test_memory_reuses_a_verified_cache_and_measures_an_asset_fold_when_nothing_is_built(tmp_path, monkeypatch):
    ctx, log = _memory_harness(tmp_path, monkeypatch, present={"d", "e"})
    out = _memory_stage().run(ctx, {})
    measured = [entry for entry in log if entry[0] == "measure"]
    assert measured == [("measure", f"{ctx.document.name}-memory-orcl")]
    assert not [entry for entry in log if entry[0] == "run"]
    assert out["measured"]["kind"] == "asset_fold"
    assert out["measured"]["name"] == "ORCL"
    assert out["passed"] is True


def test_the_asset_fold_measures_the_largest_cached_asset_across_every_group(tmp_path, monkeypatch):
    # The reading has to cover the heaviest fold in the study, so the
    # choice is over every group's cached assets, not the first group's.
    ctx, _log = _memory_harness(tmp_path, monkeypatch, present={"d", "e"})
    sizes = {"MSTR": 500}

    def largest(_path, assets):
        best = max(assets, key=lambda asset: (sizes.get(asset, 1), asset))
        return best, sizes.get(best, 1)

    seen = {}

    def measure(_ctx, document, tag):
        seen["document"] = document
        seen["tag"] = tag
        return f"/summary/{tag}", 7_000_000_000

    monkeypatch.setattr(study, "_largest_asset", largest)
    monkeypatch.setattr(study.p10, "_measure_walk", measure)
    out = _memory_stage().run(ctx, {})
    assert out["measured"] == {
        "kind": "asset_fold",
        "name": "MSTR",
        "summary_dir": f"/summary/{ctx.document.name}-memory-mstr",
        "peak_rss_bytes": 7_000_000_000,
    }
    assert seen["document"].pipeline["features"].params["path"] == out["groups"]["e"]["cache"]
    assert seen["document"].pipeline["universe"].params["path"] == "configs/universe-p12-e.json"


def test_memory_builds_only_the_missing_group_and_measures_it(tmp_path, monkeypatch):
    ctx, log = _memory_harness(tmp_path, monkeypatch, present={"d"})
    out = _memory_stage().run(ctx, {})
    assert [entry for entry in log if entry[0] in ("measure", "run")] == [("measure", f"{ctx.document.name}-cache-e")]
    assert out["measured"]["name"] == "e"


def test_memory_refuses_a_peak_at_or_above_the_limit(tmp_path, monkeypatch):
    present = set()
    ctx, _log = _memory_harness(tmp_path, monkeypatch, present=present)

    def measure(_ctx, document, tag):
        present.add(document.name.rsplit("-", 1)[-1])
        return f"/s/{tag}", 18253611008

    monkeypatch.setattr(study.p10, "_measure_walk", measure)
    with pytest.raises(MemoryError, match="strictly below"):
        _memory_stage().run(ctx, {})


def test_memory_refuses_a_cache_whose_membership_is_not_the_group(tmp_path, monkeypatch):
    ctx, _log = _memory_harness(tmp_path, monkeypatch, present=set())
    monkeypatch.setattr(study, "_verified_cache", lambda *_a: {"manifest_sha256": "a" * 64, "symbols": ["ORCL", "SPY"]})
    with pytest.raises(ValueError, match="membership"):
        _memory_stage().run(ctx, {})


def test_the_cache_verifier_checks_digests_membership_and_metadata(tmp_path):
    import numpy as np

    from intraday_equities.feature_cache import write_feature_cache

    spec = _raw("universe-p12-d.json")
    params = {"lookback": 20, "layout": "columns"}
    frames = []
    tapes = []
    for symbol in ("ORCL", "SPY"):
        frames.append({"symbol": symbol, "asof_ms": np.array([1], dtype=np.int64), "close": np.array([1.0], dtype=np.float32), "names": ["a"], "X": np.array([[1.0]], dtype=np.float32)})
        tapes.append({"symbol": symbol, "asof_ms": np.array([1], dtype=np.int64), "close": np.array([1.0], dtype=np.float32), "price_field": "close"})
    path = tmp_path / "d"
    digest = write_feature_cache(str(path), {"records": frames, "tape": tapes}, {"spec": spec, "params": params})
    assert study._verified_cache(str(tmp_path / "missing"), spec, params) is None
    got = study._verified_cache(str(path), spec, params)
    assert got == {"manifest_sha256": digest, "symbols": ["ORCL", "SPY"]}
    with pytest.raises(ValueError, match="metadata"):
        study._verified_cache(str(path), spec, {"lookback": 30, "layout": "columns"})
    with pytest.raises(ValueError, match="metadata"):
        study._verified_cache(str(path), {**spec, "lookback": 99}, params)
    # The universe node patches the derived feature-name list into the spec
    # it emits, so a real manifest's spec is the raw file plus `features`;
    # the verifier must accept that superset and refuse a changed raw key.
    emitted = {**spec, "features": ["a"]}
    path2 = tmp_path / "d2"
    digest2 = write_feature_cache(str(path2), {"records": frames, "tape": tapes}, {"spec": emitted, "params": params})
    assert study._verified_cache(str(path2), spec, params) == {"manifest_sha256": digest2, "symbols": ["ORCL", "SPY"]}
    with pytest.raises(ValueError, match="metadata"):
        study._verified_cache(str(path2), {**spec, "reference": ["QQQ"]}, params)


# -- Gate 1 ---------------------------------------------------------------------


def _caches():
    return {
        "d": {"universe": "configs/universe-p12-d.json", "universe_sha256": _digest("universe-p12-d.json"), "cache": "./c/d", "manifest_sha256": "a" * 64, "symbols": ["A", "SPY"]},
        "e": {"universe": "configs/universe-p12-e.json", "universe_sha256": _digest("universe-p12-e.json"), "cache": "./c/e", "manifest_sha256": "b" * 64, "symbols": ["B", "SPY"]},
    }


def _admit(monkeypatch, symbols):
    """Let a harness's fake cohort through the union universe's tradable list."""
    spec = study._universe_spec

    def admitted(ctx, path):
        raw = spec(ctx, path)
        return {**raw, "tradable": list(raw["tradable"]) + list(symbols)}

    monkeypatch.setattr(study, "_universe_spec", admitted)


def _gate1_harness(tmp_path, monkeypatch, passes):
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(study, "_document_cohort", lambda _document: ["A", "B"])
    _admit(monkeypatch, ["A", "B"])
    documents = []
    monkeypatch.setattr(
        study,
        "asset_walk_document",
        lambda _doc, name, asset, horizon, cache, **kw: (
            documents.append((asset, horizon, cache["cache"], kw)) or SimpleNamespace(asset=asset, horizon=horizon, name=f"{name}-x")
        ),
    )
    walks = []
    monkeypatch.setattr(study.p10, "_run_bounded_walk", lambda _ctx, doc, tag: walks.append(tag) or f"walk-{doc.asset}-{doc.horizon}")
    monkeypatch.setattr(study, "_score_one", lambda summary, asset, horizon, _alpha: {"passes": passes(asset, horizon), "t_pool": 1.0, "t_fold": 1.0, "r2oos": 0.01, "n_folds": 20})
    registered = []

    class Registry:
        def __init__(self, path):
            registered.append(("open", path))

        def record(self, key, **fields):
            registered.append((key, fields))
            return f"cell-{key['series']}-{key['horizon']}"

    monkeypatch.setattr(study, "AttemptRegistry", Registry)
    stage = study.Gate1Stage("gate1", _raw("run-p12-modelability.json")["stages"]["gate1"]["params"])
    return ctx, stage, documents, walks, registered


def test_gate1_searches_each_asset_alone_in_order_and_stops_at_the_first_failure(tmp_path, monkeypatch):
    ctx, stage, documents, walks, registered = _gate1_harness(tmp_path, monkeypatch, passes=lambda a, h: (a == "A" and h <= 2))
    out = stage.run(ctx, {"preflight": True, "caches": _caches()})
    assert [(a, h) for a, h, _c, _kw in documents] == [("A", 1), ("A", 2), ("A", 3), ("B", 1)]
    assert [c for _a, _h, c, _kw in documents] == ["./c/d", "./c/d", "./c/d", "./c/e"]
    assert walks == ["p12-40-asset-modelability-gate1-a-h01", "p12-40-asset-modelability-gate1-a-h02", "p12-40-asset-modelability-gate1-a-h03", "p12-40-asset-modelability-gate1-b-h01"]
    assert out["rows"] == [
        {"asset": "A", "gate1_h": 2, "gate1_passes": True, "first_failed_h": 3, "attempted_horizons": [1, 2, 3], "unrun_horizons": [5, 10, 20, 30, 60]},
        {"asset": "B", "gate1_h": None, "gate1_passes": False, "first_failed_h": 1, "attempted_horizons": [1], "unrun_horizons": [2, 3, 5, 10, 20, 30, 60]},
    ]
    assert [c["cell"] for c in out["cells"]] == ["cell-A-1", "cell-A-2", "cell-A-3", "cell-B-1"]
    assert registered[0] == ("open", str(CHILD / "docs/decisioning/attempts.jsonl"))
    key, fields = registered[1]
    assert key == {
        "study": "p12-40-asset-modelability",
        "architecture": "lgbm-tight-asset-local",
        "data_cut": "2026-02-28",
        "evidence": "gate1-selection",
        "row_spacing_minutes": 5,
        "score_lattice_minutes": 30,
        "series": "A",
        "horizon": 1,
    }
    assert fields["study_gate"] == "gate1" and fields["walk"] == "walk-A-1"


def test_gate1_refuses_a_failed_preflight_and_an_asset_without_a_cache(tmp_path, monkeypatch):
    ctx, stage, _d, _w, _r = _gate1_harness(tmp_path, monkeypatch, passes=lambda a, h: False)
    assert stage.validate_inputs({"preflight": False, "caches": _caches()}) != []
    assert stage.validate_inputs({"preflight": True}) != []
    assert stage.validate_inputs({"preflight": True, "caches": _caches()}) == []
    with pytest.raises(ValueError, match="no group"):
        stage.run(ctx, {"preflight": True, "caches": {"d": {**_caches()["d"], "symbols": ["SPY"]}}})


def test_the_gates_refuse_a_fit_symbol_the_universe_does_not_list_as_tradable(
    tmp_path, monkeypatch
):
    # ADR-0094 §2: the cohort is graded, but a name the document's own
    # universe does not list as tradable would be fitted against bars no
    # group cache holds. Both gates refuse before deriving any walk.
    stages = _raw("run-p12-modelability.json")["stages"]
    gate1 = study.Gate1Stage("gate1", stages["gate1"]["params"])
    declared = _raw("run-p12-modelability.json")["pipeline"]["scan"]["params"]["fit_symbols"]
    assert gate1.cohort(_ctx(tmp_path)) == declared
    ctx = _ctx(tmp_path)
    obj = ctx.document.to_obj()
    obj["pipeline"]["scan"]["params"]["fit_symbols"] = ["ORCL", "ZZZ", "MSTR", "YYY"]
    ctx.document = PipelineDocument.from_obj(obj)
    documents = []
    monkeypatch.setattr(
        study,
        "asset_walk_document",
        lambda *a, **kw: documents.append(a) or SimpleNamespace(),
    )
    with pytest.raises(ValueError, match="tradable") as refusal:
        gate1.run(ctx, {"preflight": True, "caches": _caches()})
    assert "ZZZ" in str(refusal.value) and "YYY" in str(refusal.value)
    walks = study.Gate3WalksStage("gate3_walks", stages["gate3_walks"]["params"])
    with pytest.raises(ValueError, match="tradable"):
        walks.run(ctx, {"gate1": [], "gate1_cells": [], "caches": _caches()})
    assert documents == []
    # The residual reference is not tradable anywhere, which is why P11 —
    # whose cohort holds SPY — overrides the hook instead of inheriting it.
    assert "SPY" not in _raw("universe-p12.json")["tradable"]


def test_gate1_refuses_to_run_as_of_any_date_but_its_data_cut(tmp_path, monkeypatch):
    ctx, stage, documents, _w, _r = _gate1_harness(tmp_path, monkeypatch, passes=lambda a, h: False)
    ctx.asof = "2026-03-01"
    with pytest.raises(ValueError, match="data_cut"):
        stage.run(ctx, {"preflight": True, "caches": _caches()})
    assert documents == []


def test_gate_stages_refuse_a_group_universe_that_moved_since_the_memory_stage(tmp_path, monkeypatch):
    ctx, stage, documents, _w, _r = _gate1_harness(tmp_path, monkeypatch, passes=lambda a, h: False)
    moved = _caches()
    moved["e"]["universe_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="universe"):
        stage.run(ctx, {"preflight": True, "caches": moved})
    assert documents == []
    walks = study.Gate3WalksStage("gate3_walks", _raw("run-p12-modelability.json")["stages"]["gate3_walks"]["params"])
    monkeypatch.setattr(study, "_document_cohort", lambda _document: ["A", "B"])
    gate1 = [{"asset": "A", "gate1_h": 1, "gate1_passes": True}, {"asset": "B", "gate1_h": None, "gate1_passes": False}]
    cells = [{"asset": "A", "horizon": 1, "skill": {"r2oos": 0.02, "t_pool": 2.0}}]
    with pytest.raises(ValueError, match="universe"):
        walks.run(ctx, {"gate1": gate1, "gate1_cells": cells, "caches": moved})


def test_gate1_params_are_default_deny_and_ordered():
    ok = _raw("run-p12-modelability.json")["stages"]["gate1"]["params"]
    assert study.Gate1Stage.validate_params(ok) == []
    assert study.Gate1Stage.validate_params({**ok, "data_cut": "2026-2-28"}) != []
    assert study.Gate1Stage.validate_params({k: v for k, v in ok.items() if k != "data_cut"}) != []
    assert study.Gate1Stage.validate_params({**ok, "assets": ["A"]}) != []
    assert study.Gate1Stage.validate_params({**ok, "horizons": [1, 1]}) != []
    assert study.Gate1Stage.validate_params({**ok, "horizons": [2, 1]}) != []
    assert study.Gate1Stage.validate_params({**ok, "horizons": []}) != []
    assert study.Gate1Stage.validate_params({**ok, "alpha": 1}) != []
    assert study.Gate1Stage.validate_params({**ok, "architecture": ""}) != []
    assert study.Gate1Stage.validate_params({k: v for k, v in ok.items() if k != "attempt_registry"}) != []


# -- Gate 3 ---------------------------------------------------------------------


def _gate3_harness(tmp_path, monkeypatch, nulls):
    """Two survivors at different horizons: A (h2) and B (h5); C failed Gate 1."""
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(study, "_document_cohort", lambda _document: ["A", "B", "C"])
    _admit(monkeypatch, ["A", "B", "C"])
    documents = []
    monkeypatch.setattr(
        study,
        "asset_walk_document",
        lambda _doc, name, asset, horizon, cache, **kw: (
            documents.append((asset, horizon, cache["cache"], kw["scramble_seed"])) or SimpleNamespace(asset=asset, horizon=horizon, seed=kw["scramble_seed"])
        ),
    )
    monkeypatch.setattr(study.p10, "_run_bounded_walk", lambda _ctx, doc, _tag: f"walk-{doc.asset}-{doc.horizon}-{doc.seed}")

    def score(summary, asset, _horizon, _alpha):
        seed = int(str(summary).rsplit("-", 1)[-1])
        return {"r2oos": nulls[asset][seed], "t_pool": 0.1 * seed}

    monkeypatch.setattr(study, "_score_one", score)
    gate1 = [
        {"asset": "A", "gate1_h": 2, "gate1_passes": True},
        {"asset": "B", "gate1_h": 5, "gate1_passes": True},
        {"asset": "C", "gate1_h": None, "gate1_passes": False},
    ]
    cells = [
        {"asset": "A", "horizon": 2, "skill": {"r2oos": 0.02, "t_pool": 2.0}},
        {"asset": "B", "horizon": 5, "skill": {"r2oos": 0.03, "t_pool": 2.0}},
    ]
    caches = {**_caches(), "d": {**_caches()["d"], "symbols": ["A", "C", "SPY"]}}
    monkeypatch.setattr(study.p10, "_child_root", lambda _ctx: str(CHILD))
    params = _raw("run-p12-modelability.json")["stages"]["gate3_walks"]["params"]
    walks = study.Gate3WalksStage("gate3_walks", params)
    result = study.Gate3ResultStage("gate3", _raw("run-p12-modelability.json")["stages"]["gate3"]["params"])
    return ctx, walks, result, documents, gate1, cells, caches


def test_gate3_walks_stop_each_asset_on_its_own_first_exceedance(tmp_path, monkeypatch):
    nulls = {"A": {s: 0.0 for s in range(19)}, "B": {s: -0.01 for s in range(19)}}
    nulls["A"][1] = 0.02  # a tie on seed 1 stops A
    ctx, walks, _result, documents, gate1, cells, caches = _gate3_harness(tmp_path, monkeypatch, nulls)
    out = walks.run(ctx, {"gate1": gate1, "gate1_cells": cells, "caches": caches})
    assert out["draws"] == {
        "A": {"stopped": True, "stop_seed": 1, "n_draws": 2},
        "B": {"stopped": False, "stop_seed": None, "n_draws": 19},
    }
    assert out["survivors"] == ["A", "B"]
    assert sorted(out["walks"]) == sorted([f"A:2:{s}" for s in range(2)] + [f"B:5:{s}" for s in range(19)])
    assert [(a, h, c) for a, h, c, _s in documents][:2] == [("A", 2, "./c/d"), ("A", 2, "./c/d")]
    assert {c for a, _h, c, _s in documents if a == "B"} == {"./c/e"}
    assert walks.validate_inputs({"gate1": [], "gate1_cells": []}) != []
    assert walks.validate_inputs({"gate1": [], "gate1_cells": [], "caches": {}}) == []


def test_gate3_walks_refuse_before_spawning_when_a_survivor_has_no_scored_cell(tmp_path, monkeypatch):
    nulls = {"A": {s: 0.0 for s in range(19)}, "B": {s: 0.0 for s in range(19)}}
    ctx, walks, _result, documents, gate1, cells, caches = _gate3_harness(tmp_path, monkeypatch, nulls)
    with pytest.raises(ValueError, match="Gate-1 cell"):
        walks.run(ctx, {"gate1": gate1, "gate1_cells": cells[:1], "caches": caches})
    assert documents == []


def test_gate3_result_decides_each_asset_from_its_own_record(tmp_path, monkeypatch):
    nulls = {"A": {s: 0.0 for s in range(19)}, "B": {s: -0.01 for s in range(19)}}
    ctx, _walks, result, _documents, gate1, cells, _caches = _gate3_harness(tmp_path, monkeypatch, nulls)
    seen = []
    monkeypatch.setattr(study, "tier2_verdict", lambda observed, r2, ts: seen.append((observed, list(r2), list(ts))) or {"passes": True, "beat_all": True})
    draws = {"A": {"stopped": True, "stop_seed": 1, "n_draws": 2}, "B": {"stopped": False, "stop_seed": None, "n_draws": 19}}
    walks = {**{f"A:2:{s}": f"walk-A-2-{s}" for s in range(2)}, **{f"B:5:{s}": f"walk-B-5-{s}" for s in range(19)}}
    out = result.run(ctx, {"gate1": gate1, "gate1_cells": cells, "walks": walks, "draws": draws})
    a, b, c = out["rows"]
    assert (a["asset"], a["gate3_status"], a["stopped"], a["stop_seed"], a["n_draws"], a["p_bound"]) == ("A", "fail", True, 1, 2, 2 / 3)
    assert a["null_mean"] is None and a["null_sd"] is None and a["calibration"] == "not_computed_early_stop" and "gate3" not in a
    assert (b["asset"], b["gate3_status"], b["gate3_passes"]) == ("B", "pass", True)
    assert b["gate3"] == {"passes": True, "beat_all": True} and "stopped" not in b
    assert seen == [(0.03, [-0.01] * 19, [0.1 * s for s in range(19)])]
    assert (c["asset"], c["gate3_status"], c["not_reached_reason"]) == ("C", "not_reached", "gate1_failed_at_h1")
    assert result.validate_inputs({"gate1": [], "gate1_cells": [], "walks": {}}) != []
    with pytest.raises(ValueError, match="draws"):
        result.run(ctx, {"gate1": gate1, "gate1_cells": cells, "walks": walks, "draws": {"A": draws["A"]}})


def test_gate3_params_take_any_seed_list_but_refuse_a_malformed_one():
    ok = _raw("run-p12-modelability.json")["stages"]["gate3_walks"]["params"]
    assert study.Gate3WalksStage.validate_params(ok) == []
    assert study.Gate3WalksStage.validate_params({**ok, "seeds": [0, 1, 2]}) == []
    assert study.Gate3WalksStage.validate_params({**ok, "seeds": []}) != []
    assert study.Gate3WalksStage.validate_params({**ok, "seeds": [0, 0]}) != []
    assert study.Gate3WalksStage.validate_params({**ok, "seeds": [-1]}) != []
    assert study.Gate3WalksStage.validate_params({**ok, "seeds": [True]}) != []
    assert study.Gate3WalksStage.validate_params({**ok, "assets": ["A"]}) != []
    assert study.Gate3ResultStage.validate_params({**ok, "assets": ["A"]}) != []


# -- P11 is a pinned special case of the same study ----------------------------


def test_p11_stages_are_subclasses_of_the_study_and_keep_their_contract():
    from intraday_equities import modelability_p11 as p11

    assert issubclass(p11.MemoryPreflightStage, study.MemoryPreflightStage)
    assert issubclass(p11.Gate1Stage, study.Gate1Stage)
    assert issubclass(p11.Gate3WalksStage, study.Gate3WalksStage)
    assert issubclass(p11.Gate3ResultStage, study.Gate3ResultStage)
    assert p11.MemoryPreflightStage._PARAMS == ("assets", "memory_limit_bytes")
    assert p11.Gate1Stage._PARAMS == ("assets", "horizons", "attempt_registry", "alpha")
    assert p11.Gate3WalksStage._PARAMS == ("seeds", "alpha")
    assert p11.Gate3ResultStage._PARAMS == ("assets", "seeds", "alpha")
    assert p11.Gate1Stage.outputs == study.Gate1Stage.outputs == ("rows", "cells")
    assert p11.Gate3WalksStage.outputs == study.Gate3WalksStage.outputs == ("walks", "survivors", "draws")
    assert p11.Gate3ResultStage.outputs == study.Gate3ResultStage.outputs == ("rows",)
    assert study.MemoryPreflightStage.outputs == ("groups", "measured", "limit_bytes", "passed")
    assert p11.MemoryPreflightStage.outputs == (
        "asset",
        "feature_rows",
        "summary_dir",
        "peak_rss_bytes",
        "limit_bytes",
        "feature_cache_manifest_sha256",
        "passed",
    )
    assert len(p11._ASSETS) == 25
    assert p11.Gate1Stage("gate1", {"assets": p11._ASSETS, "horizons": p11._HORIZONS, "attempt_registry": "x", "alpha": 0.05}).validate_inputs({"preflight": True}) == []
