"""ADR-0182 S5: the point-in-time forecast publisher over a synthetic three-fold walk.

The fixture is a miniature P16 walk written with the real writers
(``PredictionWriter``, ``write_feature_cache``) and the real universe
file: three contiguous four-day folds, four modeled names at leads 1..3,
and a gate artifact admitting LLY and LRCX at h2 and NOW at h1 (ANET is
modeled but not admitted). Its stored ``y`` is the fold label's own
value, so the publisher's sigma/beta can be checked against it.
"""

import hashlib
import json
import math
import os
import shutil
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest
from dskit.pipeline.document import PipelineDocument
from dskit.pipeline.false_signal import GrenanderLocalFdr
from dskit.pipeline.node import DEFAULT_NODE_KINDS, NodeContext, class_ref
from dskit.pipeline.predictions import PredictionWriter
from dskit.pipeline.uncertainty_intake import DecisionDemand, admission_problems

from intraday_equities.feature_cache import write_feature_cache
from intraday_equities.final_gates import DEVELOPMENT_EVIDENCE_SCOPE
from intraday_equities.forecast_bundle import ConfirmedCaps, ForecastBundle
from intraday_equities.nodes import Universe, _child_root, _label_from_params, _tapes_from_bars
from intraday_equities.nodes_capital import REQUIRED_INTAKES
from intraday_equities.simulation import ForecastPublisher, _Walk

KIND = "intraday_equities-forecast-publisher"
FIRST = date(2022, 5, 6)
STEP = 4
COUNT = 3
NAMES = ("ANET", "LLY", "LRCX", "NOW")
CAPS = {"ANET": 0, "LLY": 2, "LRCX": 2, "NOW": 1}
LEADS = (1, 2, 3)
LABEL = {"label_scale": "vol", "label_residual": "SPY", "label_residual_self": "raw"}
UNIVERSE = os.path.join(_child_root(), "configs", "universe-p13-pooled.json")
OPEN_MS = (14 * 60 + 30) * 60_000
DOC = "e" * 64


def _ms(day):
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)


def _sha(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _dump(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True)


def _tape():
    """One-minute sessions from 16 days before the first cutoff to past the last fold."""
    rng = np.random.default_rng(7)
    days = [FIRST - timedelta(days=16 - i) for i in range(16 + STEP * COUNT + 2)]
    stamps = np.array(
        [_ms(day) + OPEN_MS + i * 60_000 for day in days for i in range(390)], dtype=np.int64
    )
    market = rng.normal(0.0, 8e-4, stamps.size)
    prices = {"SPY": 400.0 * np.exp(np.cumsum(market))}
    for name in NAMES:
        prices[name] = 100.0 * np.exp(np.cumsum(0.8 * market + rng.normal(0.0, 6e-4, stamps.size)))
    return stamps, prices


def _splits(index):
    cutoff = FIRST + timedelta(days=STEP * index)
    embargo = cutoff - timedelta(days=1)
    return {
        "kind": "time",
        "train_start_ms": _ms(embargo - timedelta(days=10)),
        "train_end_ms": _ms(embargo) - 1,
        "val_start_ms": _ms(cutoff),
        "val_end_ms": _ms(cutoff + timedelta(days=STEP)) - 1,
        "test_end_ms": _ms(cutoff + timedelta(days=STEP)),
    }


def _write_predictions(fx):
    for (index, lead), block in sorted(fx["data"].items()):
        directory = os.path.join(fx["runs"][index], "artifacts", f"scan_h{lead:02d}")
        shutil.rmtree(directory, ignore_errors=True)
        with PredictionWriter(directory, list(NAMES), fold=index, period_minutes=30) as writer:
            for name in NAMES:
                stamps, y, yhat = block[name]
                writer.append(name, lead, list(stamps), list(y), list(yhat), 0.01)


def _write_inventory(fx):
    folds = []
    for index, run_dir in enumerate(fx["runs"]):
        paths = sorted(
            os.path.realpath(os.path.join(run_dir, "artifacts", f"scan_h{lead:02d}", "predictions.parquet"))
            for lead in LEADS
        )
        folds.append(
            {
                "cutoff": (FIRST + timedelta(days=STEP * index)).isoformat(),
                "run_dir": run_dir,
                "predictions": [{"path": p, "sha256": _sha(p)} for p in paths],
            }
        )
    manifest = {
        "schema_version": 1,
        "evidence_scope": DEVELOPMENT_EVIDENCE_SCOPE,
        "winner": {"id": "fixture-model"},
        "expected_units": [{"symbol": n, "horizon": h} for n in NAMES for h in LEADS],
        "folds": folds,
    }
    _dump(fx["inventory"], {"state": "ran", "stage": "inventory", "outputs": {"manifest": manifest}})
    fx["params"]["inventory_manifest_sha256"] = _sha(fx["inventory"])


def _repin(fx, mutate):
    for (index, lead), block in fx["data"].items():
        for name in NAMES:
            stamps, y, yhat = block[name]
            block[name] = (stamps, *mutate(index, name, lead, stamps, y, yhat))
    _write_predictions(fx)
    _write_inventory(fx)


@pytest.fixture
def fx(tmp_path):
    """A pinned three-fold walk, its gate artifact, calendar and a run dir."""
    root = str(tmp_path)
    stamps, prices = _tape()
    cache = os.path.join(root, "walk", "pipeline_cache", "a")
    frames = [
        {"symbol": s, "names": ["f0"], "asof_ms": stamps[:5], "close": prices[s][:5], "X": np.zeros((5, 1))}
        for s in ("SPY",) + NAMES
    ]
    tapes = [
        {"symbol": s, "asof_ms": stamps, "close": prices[s], "price_field": "close"}
        for s in ("SPY",) + NAMES
    ]
    cache_sha = write_feature_cache(cache, {"records": frames, "tape": tapes}, {"fixture": True})
    spec = Universe("universe", {"path": UNIVERSE}).run(None, {})["spec"]
    universe_sha = Universe("universe", {"path": UNIVERSE}).fingerprint()["sha256"]
    rng = np.random.default_rng(11)
    fx = {"runs": [], "data": {}, "tape": (stamps, prices), "inventory": os.path.join(root, "inv", "stages", "inventory.json")}
    for index in range(COUNT):
        splits = _splits(index)
        cutoff = FIRST + timedelta(days=STEP * index)
        run_dir = os.path.join(root, "runs", f"fixture-wf-{cutoff.isoformat()}")
        pipeline = {
            "universe": {"uses": "intraday_equities-universe", "params": {"path": UNIVERSE}},
            "features_a": {
                "uses": "intraday_equities-session-feature-cache",
                "params": {"path": "./pipeline_cache/a", "manifest_sha256": cache_sha},
            },
        }
        for lead in LEADS:
            pipeline[f"scan_h{lead:02d}"] = {
                "uses": "intraday_equities-no-information-scan",
                "params": {**LABEL, "lead_start": lead, "val_end_ms": "$splits.val_end_ms"},
            }
        config = {"name": f"fixture-wf-{cutoff.isoformat()}", "pipeline": pipeline, "splits": splits}
        resolved = {
            "document_hash": PipelineDocument.from_obj(config).hash,
            "run_hash": hashlib.sha256(run_dir.encode()).hexdigest(),
            "splits": splits,
            "data_fingerprint": {
                "features_a": {"manifest_sha256": cache_sha},
                "universe": {"sha256": universe_sha},
            },
        }
        _dump(os.path.join(run_dir, "config.json"), config)
        _dump(os.path.join(run_dir, "resolved.json"), resolved)
        fx["runs"].append(run_dir)
        arrays = _tapes_from_bars(tapes, "close", splits["val_end_ms"])
        label = _label_from_params(LABEL, arrays, int(spec["period_ms"]))
        lattice = np.array(
            [_ms(cutoff + timedelta(days=d)) + OPEN_MS + m * 1_800_000 for d in range(STEP) for m in range(1, 13)],
            dtype=np.int64,
        )
        loc = np.searchsorted(stamps, lattice)
        for lead in LEADS:
            block = {}
            for name in NAMES:
                y = label.values(name, loc, loc + lead)
                keep = np.isfinite(y)
                assert keep.all()
                yhat = 0.5 * y + rng.normal(0.0, 0.8, y.size)
                block[name] = (lattice.tolist(), y.tolist(), yhat.tolist())
            fx["data"][(index, lead)] = block
    fx["params"] = {
        "inventory_manifest": fx["inventory"],
        "gates": os.path.join(root, "gates", "stages", "gates.json"),
        "program_calendar": os.path.join(root, "calendar.json"),
        "fold_schedule": "development_outer",
        "walk_root": os.path.join(root, "walk"),
        "first_fold": 2,
        "last_fold": 2,
        "calibration_window_days": 30,
        "uncertainty": {"n_scenarios": 8, "coverage": 0.6, "window_blocks": 2, "null_draws": 199, "seed": 0},
    }
    _write_predictions(fx)
    _write_inventory(fx)
    caps = [{"unit": n, "capped_horizon": CAPS[n]} for n in NAMES]
    _dump(
        fx["params"]["gates"],
        {
            "state": "ran",
            "outputs": {
                "caps": caps,
                "metrics": {
                    "manifest_artifact": fx["inventory"],
                    "evidence_scope": DEVELOPMENT_EVIDENCE_SCOPE,
                    "deployment_eligible": False,
                },
            },
        },
    )
    fx["params"]["gates_sha256"] = _sha(fx["params"]["gates"])
    with open(os.path.join(_child_root(), "configs", "program-calendar.json"), encoding="utf-8") as handle:
        calendar = json.load(handle)
    calendar["fold_schedules"]["development_outer"].update(
        {"first": FIRST.isoformat(), "step_days": STEP, "count": COUNT, "val_days": STEP,
         "embargo_days": 1, "train_days": 10,
         "last_validation_end_exclusive": (FIRST + timedelta(days=STEP * COUNT)).isoformat()}
    )
    _dump(fx["params"]["program_calendar"], calendar)
    run = os.path.join(root, "run")
    _dump(os.path.join(run, "resolved.json"), {"document_hash": DOC})
    fx["ctx"] = NodeContext(name="sim", asof="2022-06-01", run_dir=run)
    return fx


def _run(fx):
    return ForecastPublisher("publish", fx["params"]).run(fx["ctx"], {})


def _canon(value):
    return json.dumps(value, sort_keys=True)


def test_segment_artifacts_ignore_rows_at_or_after_cutoff(fx):
    before = _run(fx)
    cutoff = before["releases"][0]["segment_start_ms"]

    def late(index, name, lead, stamps, y, yhat):
        return (
            [v * 5.0 + 1.0 if s >= cutoff else v for s, v in zip(stamps, y)],
            [-3.0 * v if s >= cutoff else v for s, v in zip(stamps, yhat)],
        )

    _repin(fx, late)
    after = _run(fx)
    assert _canon(after["releases"]) == _canon(before["releases"])
    assert _canon(after["bundles"]) != _canon(before["bundles"])

    _repin(fx, lambda i, n, lead, s, y, yhat: ([v + 0.5 for v in y] if i == 1 else y, yhat))
    assert _canon(_run(fx)["releases"]) != _canon(before["releases"])


def test_tick_path_never_reads_realized_y(fx):
    before = _run(fx)
    _repin(fx, lambda i, n, lead, s, y, yhat: ([v * -7.0 for v in y] if i == 2 else y, yhat))
    after = _run(fx)
    assert _canon(after["bundles"]) == _canon(before["bundles"])
    assert _canon(after["releases"]) == _canon(before["releases"])
    _repin(fx, lambda i, n, lead, s, y, yhat: (y, [v + 0.25 for v in yhat] if i == 2 else yhat))
    assert _canon(_run(fx)["bundles"]) != _canon(before["bundles"])


def test_fold_train_end_after_cutoff_refuses(fx):
    path = os.path.join(fx["runs"][1], "resolved.json")
    with open(path, encoding="utf-8") as handle:
        resolved = json.load(handle)
    resolved["splits"]["train_end_ms"] = resolved["splits"]["val_start_ms"]
    _dump(path, resolved)
    with pytest.raises(ValueError, match="train_end_ms .* is not before its cutoff"):
        _run(fx)


def test_prediction_pin_mismatch_refuses(fx):
    for block in fx["data"].values():
        stamps, y, yhat = block["LLY"]
        block["LLY"] = (stamps, y, [v + 1.0 for v in yhat])
    _write_predictions(fx)
    with pytest.raises(ValueError, match="prediction artifact hash changed"):
        _run(fx)


def test_sigma_reproduces_the_stored_label(fx):
    node = ForecastPublisher("publish", fx["params"])
    walk = _Walk(fx["params"])
    try:
        release, scenarios = node._release(walk, 2, DOC)
        ticks = node._tick_rows(walk, 2, release, scenarios)
    finally:
        walk.close()
    stamps, prices = fx["tape"]
    checked = 0
    for (stamp, lead), rows in ticks.items():
        for row in rows:
            loc = int(np.searchsorted(stamps, stamp))
            own = math.log(prices[row["entity"]][loc + lead] / prices[row["entity"]][loc])
            ref = math.log(prices["SPY"][loc + lead] / prices["SPY"][loc])
            y = (own - row["beta_t"] * ref) / (row["sigma_t"] * math.sqrt(lead))
            stored_stamps, stored_y, _ = fx["data"][(2, lead)][row["entity"]]
            stored = stored_y[stored_stamps.index(stamp)]
            assert y == pytest.approx(float(np.float32(stored)), rel=1e-5, abs=1e-6)
            assert row["price"] == prices[row["entity"]][loc]
            checked += 1
    assert checked == 3 * STEP * 12


def test_artifacts_admitted_in_segment_and_refused_before_cutoff(fx):
    release = json.loads(json.dumps(_run(fx)["releases"][0]))
    members = dict(REQUIRED_INTAKES)
    for lead in release["lead_groups"]:
        envelopes = ForecastPublisher.envelopes(release, lead)
        for when, admitted in (
            (release["segment_start_ms"] + 3_600_000, True),
            (release["segment_start_ms"] - 1, False),
        ):
            demand = DecisionDemand(
                decision_ts_ms=when,
                model_identity=release["release_id"],
                max_calibration_age_ms=64 * 86_400_000,
                min_measured_coverage=0.01,
            )
            for slot, envelope in envelopes.items():
                problems = admission_problems(envelope, demand, members[slot])
                if admitted:
                    assert problems == []
                else:
                    assert any(p.startswith("post_decision") for p in problems)


def test_cap_never_carries_the_p16_scope(fx):
    release = _run(fx)["releases"][0]
    cap = release["cap"]
    assert ConfirmedCaps.problems(cap) == []
    assert cap["evidence_scope"] != DEVELOPMENT_EVIDENCE_SCOPE
    assert "post-selection" in cap["evidence_scope"]
    assert cap["deployment_eligible"] is False
    assert cap["evidence_end_ms"] == _splits(COUNT - 1)["val_end_ms"]
    assert cap["generated_ms"] == os.stat(fx["params"]["gates"]).st_mtime_ns // 1_000_000
    assert cap["generated_ms"] > cap["evidence_end_ms"]
    assert cap["evidence"]["sha256"] == fx["params"]["gates_sha256"]
    assert cap["model_release_id"] == release["release_id"]
    assert cap["caps"] == [
        {"symbol": "LLY", "capped_horizon": 2},
        {"symbol": "LRCX", "capped_horizon": 2},
        {"symbol": "NOW", "capped_horizon": 1},
    ]


def test_only_gate_admitted_units_at_capped_horizon_enter_bundles(fx):
    out = _run(fx)
    units = {(row["entity"], row["lead"]) for bundle in out["bundles"] for row in bundle["rows"]}
    assert units == {("LLY", 2), ("LRCX", 2), ("NOW", 1)}
    assert out["releases"][0]["survivors"] == ["LLY", "LRCX", "NOW"]
    assert out["releases"][0]["lead_map"] == {"LLY": 2, "LRCX": 2, "NOW": 1}


def test_gates_sha_or_manifest_mismatch_refuses(fx):
    wrong = dict(fx["params"], gates_sha256="0" * 64)
    with pytest.raises(ValueError, match="gate artifact hash changed"):
        ForecastPublisher("publish", wrong).run(fx["ctx"], {})
    with open(fx["params"]["gates"], encoding="utf-8") as handle:
        gates = json.load(handle)
    gates["outputs"]["metrics"]["manifest_artifact"] = "/elsewhere/inventory.json"
    other = fx["params"]["gates"] + ".other.json"
    _dump(other, gates)
    moved = dict(fx["params"], gates=other, gates_sha256=_sha(other))
    with pytest.raises(ValueError, match="not the pinned inventory"):
        ForecastPublisher("publish", moved).run(fx["ctx"], {})


def test_one_bundle_per_lead_group_never_mixed(fx):
    out = _run(fx)
    bundles = out["bundles"]
    cutoff = out["releases"][0]["segment_start_ms"]
    keys = [(b["decision_ts"], b["lead"]) for b in bundles]
    assert len(keys) == len(set(keys))
    for bundle in bundles:
        assert {row["lead"] for row in bundle["rows"]} == {bundle["lead"]}
        assert {row["decision_ts"] for row in bundle["rows"]} == {bundle["decision_ts"]}
        for row in bundle["rows"]:
            assert row["producer"] == {"document_sha256": DOC, "node": "publish", "output": "bundle"}
            stamp = bundle["decision_ts"]
            assert row["known_at"] == {
                "sigma": stamp, "beta": stamp, "reference": stamp, "price": stamp, "yhat": stamp,
                "pi_hat": cutoff, "pi_widened": cutoff, "scenarios": cutoff,
            }
    by_tick = {}
    for stamp, lead in keys:
        by_tick.setdefault(stamp, set()).add(lead)
    assert all(leads == {1, 2} for leads in by_tick.values())
    assert {row["entity"] for b in bundles if b["lead"] == 2 for row in b["rows"]} == {"LLY", "LRCX"}


def test_false_signal_projection_preserves_values(fx):
    node = ForecastPublisher("publish", fx["params"])
    release = node.run(fx["ctx"], {})["releases"][0]
    walk = _Walk(fx["params"])
    try:
        estimate = node._false_signal(walk, 2)
    finally:
        walk.close()
    assert len(estimate.pi_hat) == len(NAMES) * len(LEADS)
    for symbol, lead in release["lead_map"].items():
        artifact = release["uncertainty"][str(lead)]["false_signal"]["artifact"]
        cell = f"{symbol}:h{lead:02d}"
        assert artifact["pi_hat"][symbol] == estimate.pi_hat[cell]
        assert artifact["pi_widened"][symbol] == estimate.pi_widened[cell]
        assert artifact["evidence"]["estimator"] == class_ref(GrenanderLocalFdr)
        assert artifact["evidence"]["pvalues"] == dict(estimate.evidence["pvalues"])


def test_outputs_are_json_and_the_kind_is_registered(fx):
    out = _run(fx)
    assert json.loads(json.dumps(out)) == out
    for bundle in out["bundles"]:
        ForecastBundle.digest(bundle["rows"])
    assert DEFAULT_NODE_KINDS.get(KIND)[0] is ForecastPublisher
    assert ForecastPublisher.validate_params(dict(fx["params"], extra=1))
    assert ForecastPublisher.validate_params(dict(fx["params"], first_fold=1))
    params = dict(fx["params"])
    params["uncertainty"] = dict(params["uncertainty"], null_draws=10)
    assert ForecastPublisher.validate_params(params)
    assert ForecastPublisher.validate_params(fx["params"]) == []
