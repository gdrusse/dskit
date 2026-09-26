"""ADR-0184 S5/S6: the forecast publisher and the per-tick MIO decider on a synthetic walk.

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
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from decimal import Decimal
from zoneinfo import ZoneInfo

import numpy as np
import pytest
from dskit.pipeline.document import PipelineDocument
from dskit.pipeline.false_signal import GrenanderLocalFdr
from dskit.pipeline.node import DEFAULT_NODE_KINDS, ConfigError, NodeContext, class_ref
from dskit.pipeline.predictions import TRADE_PREDICTIONS_FILE, PredictionWriter
from dskit.pipeline.uncertainty_intake import DecisionDemand, admission_problems

import intraday_equities.simulation as simulation_module
from intraday_equities.feature_cache import write_feature_cache
from intraday_equities.final_gates import DEVELOPMENT_EVIDENCE_SCOPE
from intraday_equities.forecast_bundle import ConfirmedCaps, ForecastBundle
from intraday_equities.model_zoo import TRADE_CACHE_PREFIX
from intraday_equities.nodes import Universe, _child_root, _label_from_params, _tapes_from_bars
from intraday_equities.nodes_capital import CAP_LOOK_AHEAD_DISCLOSURE, REQUIRED_INTAKES, EquityKellyMIO
from intraday_equities.replay import CashFlowPolicy, DevelopmentReplay, EquityReplay, FillPolicy
from intraday_equities.simulation import (
    DevelopmentSimulation,
    ForecastPublisher,
    MioDecider,
    MioDeciderNode,
    SimulationReport,
    _Walk,
)

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


def _tape(count=COUNT):
    """One-minute sessions from 16 days before the first cutoff to past the last fold."""
    rng = np.random.default_rng(7)
    days = [FIRST - timedelta(days=16 - i) for i in range(16 + STEP * count + 2)]
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
        row = {
            "cutoff": (FIRST + timedelta(days=STEP * index)).isoformat(),
            "run_dir": run_dir,
            "predictions": [{"path": p, "sha256": _sha(p)} for p in paths],
        }
        if fx.get("minute"):
            trade = sorted(
                os.path.realpath(os.path.join(run_dir, "artifacts", f"scan_h{lead:02d}", TRADE_PREDICTIONS_FILE))
                for lead in LEADS
            )
            row["trade_predictions"] = [{"path": p, "sha256": _sha(p)} for p in trade]
        folds.append(row)
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
    if fx.get("minute"):
        _write_trade(fx)
    _write_inventory(fx)


def _write_trade(fx, mutate=None):
    """ADR-0186: every tape minute of each fold's validation window, per name and lead.

    The lattice minutes carry the scored ``yhat`` (the scan's invariant);
    every other minute its own seeded draw. ``mutate(index, name, lead,
    stamps, yhat) -> (stamps, yhat)`` may rewrite a block before it is written.
    """
    stamps, _ = fx["tape"]
    nan = float("nan")
    for index, run_dir in enumerate(fx["runs"]):
        splits = _splits(index)
        window = stamps[(stamps >= splits["val_start_ms"]) & (stamps <= splits["val_end_ms"])]
        for lead in LEADS:
            rng = np.random.default_rng(1000 * index + lead)
            directory = os.path.join(run_dir, "artifacts", f"scan_h{lead:02d}")
            block = fx["data"][(index, lead)]
            with PredictionWriter(
                directory, list(NAMES), fold=index, period_minutes=1, filename=TRADE_PREDICTIONS_FILE,
            ) as writer:
                for name in NAMES:
                    scored = dict(zip(block[name][0], block[name][2]))
                    yhat = [scored[int(t)] if int(t) in scored else float(rng.normal(0.0, 0.8)) for t in window]
                    kept = window.tolist()
                    if mutate is not None:
                        kept, yhat = mutate(index, name, lead, kept, yhat)
                    writer.append(name, lead, kept, [nan] * len(yhat), yhat, nan)


@pytest.fixture
def fx(tmp_path):
    """A pinned three-fold walk, its gate artifact, calendar and a run dir."""
    return _build_fx(str(tmp_path))


def _build_fx(root, count=COUNT, minute=False):
    """Write the pinned ``count``-fold walk under ``root``; return its handles.

    ``minute`` also writes every fold's per-minute trade predictions and
    pins them in the inventory (ADR-0186).
    """
    stamps, prices = _tape(count)
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
    trade_sha = None
    if minute:
        # A minute walk's fold also names its trade cache (ADR-0186). Its
        # tapes are deliberately NOT the scored cache's, so a publisher that
        # read label tapes from it would refuse ("different ... tapes").
        trade_sha = write_feature_cache(
            os.path.join(root, "walk", "pipeline_cache", "trade"),
            {"records": frames, "tape": [{**t, "close": t["close"] * 2.0} for t in tapes]},
            {"fixture": "trade"},
        )
    spec = Universe("universe", {"path": UNIVERSE}).run(None, {})["spec"]
    universe_sha = Universe("universe", {"path": UNIVERSE}).fingerprint()["sha256"]
    rng = np.random.default_rng(11)
    fx = {"runs": [], "data": {}, "tape": (stamps, prices), "minute": minute,
          "inventory": os.path.join(root, "inv", "stages", "inventory.json")}
    for index in range(count):
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
        if trade_sha is not None:
            pipeline[f"{TRADE_CACHE_PREFIX}a"] = {
                "uses": "intraday_equities-session-feature-cache",
                "params": {"path": "./pipeline_cache/trade", "manifest_sha256": trade_sha},
            }
        config = {"name": f"fixture-wf-{cutoff.isoformat()}", "pipeline": pipeline, "splits": splits}
        resolved = {
            "document_hash": PipelineDocument.from_obj(config).hash,
            "run_hash": hashlib.sha256(run_dir.encode()).hexdigest(),
            "splits": splits,
            "data_fingerprint": {
                "features_a": {"manifest_sha256": cache_sha},
                "universe": {"sha256": universe_sha},
                **({f"{TRADE_CACHE_PREFIX}a": {"manifest_sha256": trade_sha}} if trade_sha else {}),
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
    if minute:
        _write_trade(fx)
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
        {"first": FIRST.isoformat(), "step_days": STEP, "count": count, "val_days": STEP,
         "embargo_days": 1, "train_days": 10,
         "last_validation_end_exclusive": (FIRST + timedelta(days=STEP * count)).isoformat()}
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


def test_tick_path_never_reads_realized_y(fx, monkeypatch):
    import pyarrow.parquet as pq

    walk = _Walk(fx["params"])
    requested = []
    real = pq.read_table

    def spy(path, **kwargs):
        requested.append(kwargs.get("columns"))
        return real(path, **kwargs)

    try:
        monkeypatch.setattr(pq, "read_table", spy)
        assert walk.yhat(2)
    finally:
        monkeypatch.undo()
        walk.close()
    assert requested and all(cols is not None and "y" not in cols for cols in requested)

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


def test_non_contiguous_folds_refuse(fx):
    # The calendar's validation window is one day shorter than its step, so
    # every fold's own geometry matches the calendar but fold k+1 does not
    # start where fold k ended.
    with open(fx["params"]["program_calendar"], encoding="utf-8") as handle:
        calendar = json.load(handle)
    calendar["fold_schedules"]["development_outer"].update(
        {"val_days": STEP - 1,
         "last_validation_end_exclusive": (FIRST + timedelta(days=STEP * COUNT - 1)).isoformat()}
    )
    _dump(fx["params"]["program_calendar"], calendar)
    for index, run_dir in enumerate(fx["runs"]):
        path = os.path.join(run_dir, "resolved.json")
        with open(path, encoding="utf-8") as handle:
            resolved = json.load(handle)
        cutoff = FIRST + timedelta(days=STEP * index)
        resolved["splits"]["val_end_ms"] = _ms(cutoff + timedelta(days=STEP - 1)) - 1
        _dump(path, resolved)
    with pytest.raises(ValueError, match="fold 1 does not start where fold 0 ended"):
        _run(fx)


def test_release_fold_rows_before_its_cutoff_never_reach_it(fx):
    # Fold 2's stored rows also carry the lattice day BEFORE its cutoff.
    # Those rows belong to no earlier release and to no tick of segment 2:
    # they must not enter its bundles, its outcome band (folds < k only) or
    # its false-signal estimate (folds < k only).
    day = FIRST + timedelta(days=STEP * 2 - 1)
    pre = [_ms(day) + OPEN_MS + m * 1_800_000 for m in range(1, 13)]
    for lead in LEADS:
        block = fx["data"][(2, lead)]
        for name in NAMES:
            stamps, y, yhat = block[name]
            block[name] = (pre + stamps, [0.3] * len(pre) + y, [0.9] * len(pre) + yhat)
    _write_predictions(fx)
    _write_inventory(fx)
    before = _run(fx)
    cutoff = before["releases"][0]["segment_start_ms"]
    assert max(pre) < cutoff
    assert before["bundles"] and all(b["decision_ts"] >= cutoff for b in before["bundles"])
    marked = set(pre)
    _repin(
        fx,
        lambda i, n, lead, s, y, yhat: (
            [-5.0 if i == 2 and t in marked else v for t, v in zip(s, y)],
            [7.0 if i == 2 and t in marked else v for t, v in zip(s, yhat)],
        ),
    )
    after = _run(fx)
    assert _canon(after["releases"]) == _canon(before["releases"])
    assert _canon(after["bundles"]) == _canon(before["bundles"])


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


# --- ADR-0184 S6: the per-tick MIO decider inside EquityReplay --------------

CONFIGS = os.path.join(_child_root(), "configs")
FILL_POLICY = FillPolicy.from_path(os.path.join(CONFIGS, "fill-policy.json"))
CASH_POLICY = CashFlowPolicy.from_path(os.path.join(CONFIGS, "cash-flow-policy.json"))
#: ADR-0184's development placeholders, except ``hfdr_q`` 0.9 and
#: ``uncertainty_min_coverage`` 0.05: the three-fold fixture's widened
#: false-signal rates (0.66-1.0) and short-window coverage would otherwise
#: forbid every trade, and these tests need trades to check.
MIO = {
    "risk_aversion_gamma": 2.0, "n_tangents": 32, "n_scenarios_max": 64,
    "cvar_alpha": 0.95, "cvar_limit": None, "cardinality": 5, "min_ticket": 0.0,
    "hfdr_q": 0.9, "band_bps": 10.0, "max_position_notional": 1e12,
    "bundle_max_staleness_ms": 0, "cap_max_staleness_ms": 5_529_600_000,
    "deployment_mode": False, "uncertainty_max_calibration_age_ms": 5_529_600_000,
    "uncertainty_min_coverage": 0.05, "cap_evidence_look_ahead": True,
}


def _decider(published, fx, **overrides):
    node = MioDeciderNode("decide", {"mio": {**MIO, **overrides}})
    mio = node.run(fx["ctx"], {"releases": published["releases"]})["mio"]
    return MioDecider(published["releases"][0], published["bundles"], mio, FILL_POLICY, fx["ctx"])


def _thin_bars(published, fx):
    """Each admitted name's tape bar at every lattice tick and the three minutes after it.

    Enough for a lead-2 lot's next-bar fill and its forced exit, over all
    four segment days, at a fraction of the full tape's ticks.
    """
    stamps, prices = fx["tape"]
    keep = {b["decision_ts"] + k * 60_000 for b in published["bundles"] for k in range(4)}
    return [
        {"symbol": s, "asof_ms": int(t), "open": float(p), "close": float(p), "halted": False}
        for s in published["releases"][0]["survivors"]
        for t, p in zip(stamps, prices[s])
        if int(t) in keep
    ]


def _book(fx, asof_ms, positions=None):
    """A funded, flat account at ``asof_ms`` marked at the fixture tape's closes."""
    stamps, prices = fx["tape"]
    loc = int(np.searchsorted(stamps, asof_ms))
    return {
        "asof_ms": asof_ms, "cash": 1020.0, "buying_power": 1020.0,
        "positions": dict(positions or {}),
        "mark_prices": {s: float(prices[s][loc]) for s in ("LLY", "LRCX", "NOW")},
        "gross_limit": 1020.0,
        # The thin tape's next bar: each name's fill instant (fill_bar_offset 1).
        "fill_ms": {s: asof_ms + 60_000 for s in ("LLY", "LRCX", "NOW")},
    }


def _ticks(published):
    """Decision instants that carry a bundle for every lead group, ascending."""
    leads = {}
    for bundle in published["bundles"]:
        leads.setdefault(bundle["decision_ts"], set()).add(bundle["lead"])
    return sorted(ts for ts, found in leads.items() if found == {1, 2})


class _Spy:
    """Forward to a decider, recording every ``(asof_ms, portfolio, orders)``."""

    def __init__(self, inner):
        self.inner = inner
        self.calls = []

    def decide(self, asof_ms, portfolio):
        orders = self.inner.decide(asof_ms, portfolio)
        self.calls.append((asof_ms, json.loads(json.dumps(portfolio)), list(orders)))
        return orders


class _Scripted:
    """A stub decider: fixed orders per instant, and the portfolio it saw at each tick."""

    def __init__(self, orders):
        self.orders = orders
        self.seen = {}

    def decide(self, asof_ms, portfolio):
        self.seen[asof_ms] = portfolio
        return list(self.orders.get(asof_ms, ()))


@pytest.fixture(scope="module")
def sim(tmp_path_factory):
    """One funded four-day MIO replay of segment 2, shared by the read-only checks."""
    fx = _build_fx(str(tmp_path_factory.mktemp("s6")))
    published = _run(fx)
    spy = _Spy(_decider(published, fx))
    bars = _thin_bars(published, fx)
    out = EquityReplay(FILL_POLICY, CASH_POLICY, decider=spy).run(bars)
    return {"fx": fx, "published": published, "bars": bars, "spy": spy, "out": out}


def _by_symbol(bars):
    out = {}
    for bar in sorted(bars, key=lambda b: b["asof_ms"]):
        out.setdefault(bar["symbol"], []).append(bar)
    return out


def _zero_fee_policy():
    with open(os.path.join(CONFIGS, "fill-policy.json"), encoding="utf-8") as handle:
        raw = json.load(handle)
    return FillPolicy({
        **raw, "half_spread_bps": {}, "default_half_spread_bps": 0.0,
        "taf_per_share": 0.0, "sec31_bps": 0.0,
    })


def _minute(i):
    return _ms(FIRST) + OPEN_MS + i * 60_000


def test_decider_sees_cash_after_this_bars_fills():
    bars = [
        {"symbol": "AAA", "asof_ms": _minute(i), "open": 10.0 + i, "close": 10.5 + i, "halted": False}
        for i in range(6)
    ]
    buy = {"symbol": "AAA", "asof_ms": _minute(0), "lead": 3, "qty": 10, "side": "buy"}
    stub = _Scripted({_minute(0): [buy]})
    out = EquityReplay(_zero_fee_policy(), CASH_POLICY, decider=stub).run(bars)
    assert set(stub.seen) == {_minute(i) for i in range(6)}
    assert stub.seen[_minute(0)]["cash"] == 1020.0
    # The entry fills at t1's open (11) BEFORE the decider runs at t1.
    at1 = stub.seen[_minute(1)]
    assert at1 == {
        "asof_ms": _minute(1), "cash": 910.0, "buying_power": 910.0,
        "positions": {"AAA": 10}, "mark_prices": {"AAA": 11.5},
        "gross_limit": 910.0 + 10 * 11.5, "pending": [], "fill_ms": {"AAA": _minute(2)},
    }
    # At t3 the lot exits at the next bar (index 4 = fill + lead): no longer a
    # position for the decider, still part of NAV.
    assert stub.seen[_minute(3)]["positions"] == {}
    assert stub.seen[_minute(3)]["gross_limit"] == 910.0 + 10 * 13.5
    assert stub.seen[_minute(4)]["cash"] == 910.0 + 10 * 14.0
    assert [(f["kind"], f["asof_ms"], f["price"]) for f in out["fills"]] == [
        ("entry", _minute(1), 11.0), ("exit", _minute(4), 14.0),
    ]


def test_the_decider_portfolio_carries_each_names_own_fill_bar_instant():
    # AAA trades every minute; BBB skips minutes 1-2, so its next bar after
    # minute 0 is minute 3. At the tape's last bar no fill bar exists: the
    # decision instant stands in, and the replay refuses any such decision.
    bars = [
        {"symbol": "AAA", "asof_ms": _minute(i), "open": 10.0, "close": 10.0, "halted": False}
        for i in range(5)
    ] + [
        {"symbol": "BBB", "asof_ms": _minute(i), "open": 20.0, "close": 20.0, "halted": False}
        for i in (0, 3, 4)
    ]
    stub = _Scripted({})
    EquityReplay(_zero_fee_policy(), CASH_POLICY, decider=stub).run(bars)
    assert stub.seen[_minute(0)]["fill_ms"] == {"AAA": _minute(1), "BBB": _minute(3)}
    assert stub.seen[_minute(3)]["fill_ms"] == {"AAA": _minute(4), "BBB": _minute(4)}
    assert stub.seen[_minute(4)]["fill_ms"] == {"AAA": _minute(4), "BBB": _minute(4)}
    # BBB has no bar at minute 1: no decision bar, so the decision instant stands in.
    assert stub.seen[_minute(1)]["fill_ms"] == {"AAA": _minute(2), "BBB": _minute(1)}


def test_a_fill_offset_of_two_keys_sizing_two_bars_ahead():
    bars = [
        {"symbol": "AAA", "asof_ms": _minute(i), "open": 10.0, "close": 10.0, "halted": False}
        for i in range(5)
    ]
    policy = FillPolicy({**_zero_fee_policy().to_obj(), "fill_bar_offset": 2})
    stub = _Scripted({})
    EquityReplay(policy, CASH_POLICY, decider=stub).run(bars)
    assert stub.seen[_minute(0)]["fill_ms"] == {"AAA": _minute(2)}


def test_a_halt_queued_entry_is_billed_at_the_rate_it_was_sized_at():
    """Skeptic round 1 (Major): under halt_handling 'queue' the entry lands on a
    later bar than sizing keyed. The fee keeps the SCHEDULED fill minute's
    multiplier (decision bar + fill_bar_offset), so the billed rate is still
    the one the decider was given; the price is the actual fill bar's."""
    ny = ZoneInfo("America/New_York")

    def at(hour, minute):
        return int(datetime(2025, 1, 15, hour, minute, tzinfo=ny).timestamp() * 1000)

    raw = _zero_fee_policy().to_obj()
    policy = FillPolicy({
        **raw, "halt_handling": "queue", "half_spread_bps": {"AAA": 10.0},
        "default_half_spread_bps": None, "eq_ratio": 1.0,
        "spread_time_of_day": {
            "timezone": "America/New_York", "default_multiplier": 1.0,
            "windows": [{"start_minute": 570, "end_minute": 600, "multiplier": 2.0}],
        },
    })
    bars = [
        {"symbol": "AAA", "asof_ms": at(9, 58), "open": 20.0, "close": 20.0, "halted": False},
        {"symbol": "AAA", "asof_ms": at(9, 59), "open": 20.0, "close": 20.0, "halted": True},
        {"symbol": "AAA", "asof_ms": at(10, 0), "open": 25.0, "close": 25.0, "halted": False},
        {"symbol": "AAA", "asof_ms": at(10, 1), "open": 25.0, "close": 25.0, "halted": False},
    ]
    buy = {"symbol": "AAA", "asof_ms": at(9, 58), "lead": 1, "qty": 10, "side": "buy"}
    stub = _Scripted({at(9, 58): [buy]})
    out = EquityReplay(policy, CASH_POLICY, decider=stub).run(bars)
    sized_key = stub.seen[at(9, 58)]["fill_ms"]["AAA"]
    assert sized_key == at(9, 59)
    entry = next(f for f in out["fills"] if f["kind"] == "entry")
    assert (entry["asof_ms"], entry["price"]) == (at(10, 0), 25.0)
    assert entry["fee"] / (entry["price"] * entry["qty"]) == pytest.approx(
        policy.costs.half_spread_bps("AAA", sized_key) * 1e-4
    )
    assert entry["fee"] == pytest.approx(10.0e-4 * 2.0 * 25.0 * 10)


def test_mio_orders_are_integer_shares_filled_next_bar_open(sim):
    calls = sim["spy"].calls
    orders = [order for _, _, found in calls for order in found]
    assert orders, "the fixture must trade for this test to mean anything"
    decided = {(b["decision_ts"], b["lead"]) for b in sim["published"]["bundles"]}
    by_symbol = _by_symbol(sim["bars"])
    entries = [f for f in sim["out"]["fills"] if f["kind"] == "entry"]
    refused = {(r["symbol"], r["asof_ms"], r["lead"]): r["reason"] for r in sim["out"]["refused"]}
    matched = 0
    for order in orders:
        assert type(order["qty"]) is int and order["qty"] > 0
        assert order["side"] == "buy"
        assert (order["asof_ms"], order["lead"]) in decided
        seq = by_symbol[order["symbol"]]
        nxt = seq[[b["asof_ms"] for b in seq].index(order["asof_ms"]) + 1]
        found = [
            f for f in entries
            if (f["symbol"], f["lead"], f["asof_ms"]) == (order["symbol"], order["lead"], nxt["asof_ms"])
        ]
        if found:
            assert (found[0]["qty"], found[0]["price"]) == (order["qty"], nxt["open"])
            matched += 1
        else:
            assert refused[(order["symbol"], nxt["asof_ms"], order["lead"])] == "insufficient_cash"
    assert matched == len(entries) > 0


def test_lots_force_exit_at_fill_plus_lead(sim):
    by_symbol = _by_symbol(sim["bars"])
    fills = sim["out"]["fills"]
    entries = [f for f in fills if f["kind"] == "entry"]
    exits = [f for f in fills if f["kind"] == "exit"]
    assert len(exits) == len(entries) > 0
    for entry in entries:
        seq = by_symbol[entry["symbol"]]
        due = seq[[b["asof_ms"] for b in seq].index(entry["asof_ms"]) + entry["lead"]]
        assert any(
            (f["symbol"], f["lead"], f["side"], f["qty"], f["asof_ms"], f["price"])
            == (entry["symbol"], entry["lead"], "sell", entry["qty"], due["asof_ms"], due["open"])
            for f in exits
        )


def test_mio_refusal_trades_nothing_and_is_recorded(sim):
    decider = _decider(sim["published"], sim["fx"], uncertainty_min_coverage=0.99)
    tick = _ticks(sim["published"])[0]
    assert decider.decide(tick, _book(sim["fx"], tick)) == []
    assert [(r["asof_ms"], r["lead"], r["reason"]) for r in decider.refused] == [
        (tick, 1, "mio_refused"), (tick, 2, "mio_refused"),
    ]
    assert all("coverage" in r["detail"] for r in decider.refused)
    # Refused in validation, before any solve: there is no record to keep.
    assert decider.solves == []


def test_every_mio_solve_is_kept_with_its_tick_and_lead(sim):
    from dskit.pipeline.libs.pyomo import SolveRecord

    decider = sim["spy"].inner
    assert decider.solves, "the fixture must solve for this test to mean anything"
    ticks = {asof for asof, _, _ in sim["spy"].calls}
    for row in decider.solves:
        assert row["asof_ms"] in ticks and row["lead"] in (1, 2)
        assert set(row) == {"asof_ms", "lead", *SolveRecord.field_names()}
        assert row["solver"] == "appsi_highs" and row["termination"] == "optimal"
    assert len({(row["asof_ms"], row["lead"]) for row in decider.solves}) == len(decider.solves)


def test_cash_never_negative_over_a_funded_multi_day_tape(sim):
    calls = sim["spy"].calls
    days = sorted({asof // 86_400_000 for asof, _, _ in calls})
    assert len(days) == STEP
    assert all(portfolio["cash"] >= 0.0 for _, portfolio, _ in calls)
    fills = sim["out"]["fills"]
    assert len({f["asof_ms"] // 86_400_000 for f in fills if f["kind"] == "entry"}) >= 2
    flows = sum(
        (f["price"] * f["qty"] - f["fee"]) if f["side"] == "sell" else -(f["price"] * f["qty"] + f["fee"])
        for f in fills
    )
    # The last tick is flat: its cash is the seed, $20 per trading day, and every fill.
    assert calls[-1][1]["positions"] == {}
    assert calls[-1][1]["cash"] == pytest.approx(1000.0 + 20.0 * len(days) + flows)


def test_lead_groups_share_one_cash_budget_in_ascending_order(sim, monkeypatch):
    seen = []
    real = EquityKellyMIO.run

    def spy(self, ctx, inputs):
        out = real(self, ctx, inputs)
        seen.append((inputs["bundle"][0]["lead"], inputs["portfolio"]["cash"], out["cash_after"]))
        return out

    monkeypatch.setattr(EquityKellyMIO, "run", spy)
    decider = _decider(sim["published"], sim["fx"])
    for tick in _ticks(sim["published"]):
        seen.clear()
        decider.decide(tick, _book(sim["fx"], tick))
        if seen and seen[0][2] < seen[0][1]:
            break
    else:
        pytest.fail("no tick where the first lead group buys")
    assert [lead for lead, _, _ in seen] == [1, 2]
    assert seen[0][1] == 1020.0
    assert seen[1][1] == seen[0][2]


def test_sizing_and_fills_charge_the_same_cost_keyed_on_the_fill_minute(sim, monkeypatch):
    """Owner rulings 2026-09-25: per name, and per FILL minute (the time-of-day window).

    The MIO sizes each name at the instant the replay will fill it (the
    decision bar + ``fill_bar_offset``), so an entry's billed per-share rate
    is exactly the rate its sizing charged.
    """
    sized = {}
    real = EquityKellyMIO.instruments

    def spy(self, inputs):
        out = real(self, inputs)
        portfolio = inputs["portfolio"]
        for name, row in out[1].items():
            sized[(portfolio["asof_ms"], name)] = (row, portfolio["fill_ms"][name])
        return out

    monkeypatch.setattr(EquityKellyMIO, "instruments", spy)
    decider = _decider(sim["published"], sim["fx"])
    out = EquityReplay(FILL_POLICY, CASH_POLICY, decider=decider).run(sim["bars"])
    assert len({name for _, name in sized}) >= 2, "the fixture must size several names"
    costs = FILL_POLICY.costs
    multipliers = {costs.time_of_day_multiplier(fill_ms) for _, fill_ms in sized.values()}
    assert len(multipliers) >= 2, f"the fixture must size inside and outside a window: {multipliers}"
    for (asof, name), (row, fill_ms) in sized.items():
        assert fill_ms == asof + 60_000  # the thin tape's next bar
        assert row["cost_buy"] == costs.buy_per_share(name, row["price"], fill_ms)
        assert row["cost_sell"] == costs.sell_per_share(name, row["price"], fill_ms)
        assert row["exit_cost_per_share"] == row["cost_sell"]
    entries = [f for f in out["fills"] if f["kind"] == "entry"]
    assert entries
    for fill in entries:
        row, fill_ms = sized[(fill["decision_ms"], fill["symbol"])]
        assert fill["asof_ms"] == fill_ms
        billed_rate = fill["fee"] / (fill["price"] * fill["qty"])
        assert billed_rate == pytest.approx(row["cost_buy"] / row["price"], rel=1e-12)


def test_lot_expires_before_next_lattice_decision_for_max_lead_10():
    bars = [
        {"symbol": "AAA", "asof_ms": _minute(i), "open": 10.0, "close": 10.0, "halted": False}
        for i in range(91)
    ]
    lattice = [_minute(0), _minute(30), _minute(60)]
    stub = _Scripted({
        ts: [{"symbol": "AAA", "asof_ms": ts, "lead": 10, "qty": 1, "side": "buy"}] for ts in lattice
    })
    out = EquityReplay(_zero_fee_policy(), CASH_POLICY, decider=stub).run(bars)
    assert all(stub.seen[ts]["positions"] == {} for ts in lattice)
    assert out["refused"] == [] and out["skipped"] == []
    assert [(f["kind"], f["asof_ms"]) for f in out["fills"]] == [
        ("entry", _minute(1)), ("exit", _minute(11)),
        ("entry", _minute(31)), ("exit", _minute(41)),
        ("entry", _minute(61)), ("exit", _minute(71)),
    ]


def test_thin_unit_with_open_lot_is_skipped_not_exited(sim, monkeypatch):
    seen = []
    real = EquityKellyMIO.run

    def spy(self, ctx, inputs):
        seen.append(([row["entity"] for row in inputs["bundle"]], inputs["portfolio"]["positions"]))
        return real(self, ctx, inputs)

    monkeypatch.setattr(EquityKellyMIO, "run", spy)
    decider = _decider(sim["published"], sim["fx"])
    tick = _ticks(sim["published"])[0]
    orders = decider.decide(tick, _book(sim["fx"], tick, positions={"LLY": 4}))
    assert all(o["symbol"] != "LLY" and o["side"] == "buy" for o in orders)
    assert decider.skipped == [
        {"symbol": "LLY", "asof_ms": tick, "lead": 2, "reason": "open_lot_at_decision"},
    ]
    assert seen == [(["NOW"], {}), (["LRCX"], {})]


def test_decide_node_emits_only_json_params(sim):
    out = MioDeciderNode("decide", {"mio": MIO}).run(
        sim["fx"]["ctx"], {"releases": sim["published"]["releases"]}
    )
    assert json.loads(json.dumps(out)) == out
    assert out == {"mio": {"params": MIO, "lead_groups": [1, 2]}}
    assert DEFAULT_NODE_KINDS.get("intraday_equities-mio-decider")[0] is MioDeciderNode
    assert MioDeciderNode.role == "transform"


def test_decide_node_refuses_bound_or_undeclared_params():
    check = MioDeciderNode.validate_params
    assert check({"mio": MIO}) == []
    for bound in (
        {"half_spread_bps": {"LLY": 2.2}}, {"default_half_spread_bps": 2.2}, {"eq_ratio": 0.9},
        {"spread_time_of_day": {"timezone": "UTC", "default_multiplier": 1.0, "windows": []}},
        {"cap_artifact_sha256": "a" * 64}, {"bundle_producer_node": "x"},
    ):
        assert any("must not carry" in p for p in check({"mio": {**MIO, **bound}}))
    undeclared = {k: v for k, v in MIO.items() if k != "cap_evidence_look_ahead"}
    assert any("declared true" in p for p in check({"mio": undeclared}))
    assert any("declared true" in p for p in check({"mio": {**MIO, "cap_evidence_look_ahead": False}}))
    assert any(p.startswith("mio: ") and "hfdr_q" in p for p in check({"mio": {**MIO, "hfdr_q": 1.5}}))
    assert any("deployment_mode" in p for p in check({"mio": {**MIO, "deployment_mode": True}}))
    assert check({"mio": MIO, "extra": 1})


# --- ADR-0184 S7: the simulate and report nodes, and the pipeline document --

SIM_KIND = "intraday_equities-development-simulation"
REPORT_KIND = "intraday_equities-simulation-report"
DISCLOSURE = {
    "deployment_eligible": False,
    "evidence_scope": "development_replay_post_selection",
    "cap_evidence_look_ahead": CAP_LOOK_AHEAD_DISCLOSURE,
}
#: The four-fold fixture's last validation day: segments 2 and 3 end here.
EVIDENCE_END = (FIRST + timedelta(days=STEP * 4 - 1)).isoformat()
TZ = CASH_POLICY.timezone


def _sim_params(first=2, last=3, **overrides):
    return {
        "deployment_eligible": False,
        "evidence_end": EVIDENCE_END,
        "fill_policy": "configs/fill-policy.json",
        "fill_policy_sha256": FILL_POLICY.digest(),
        "caps": "development-only",
        "cash_flow_policy": "configs/cash-flow-policy.json",
        "cash_flow_policy_sha256": CASH_POLICY.digest(),
        "first_fold": first,
        "last_fold": last,
        **overrides,
    }


def _sim_inputs(published, bars, fx):
    mio = MioDeciderNode("decide", {"mio": MIO}).run(fx["ctx"], {"releases": published["releases"]})["mio"]
    return {
        "bars": bars,
        "releases": published["releases"],
        "bundles": published["bundles"],
        "mio": mio,
    }


def _local_day(asof_ms):
    return datetime.fromtimestamp(asof_ms / 1000, tz=timezone.utc).astimezone(TZ).date().isoformat()


@pytest.fixture(scope="module")
def sim7(tmp_path_factory):
    """Two funded segments (folds 2 and 3) through the simulate and report nodes."""
    fx = _build_fx(str(tmp_path_factory.mktemp("s7")), count=4)
    fx["params"]["last_fold"] = 3
    published = _run(fx)
    bars = _thin_bars(published, fx)
    node = DevelopmentSimulation("simulate", _sim_params())
    out = node.run(fx["ctx"], _sim_inputs(published, list(bars), fx))
    ports = ("fills", "skipped", "refused", "cash", "metadata")
    report = SimulationReport("report", {}).run(
        fx["ctx"], {**{port: out[port] for port in ports}, "releases": published["releases"]}
    )
    return {"fx": fx, "published": published, "bars": bars, "out": out, "report": report}


def test_simulate_and_report_are_registered_nodes_with_json_outputs(sim7):
    assert DEFAULT_NODE_KINDS.get(SIM_KIND)[0] is DevelopmentSimulation
    assert DEFAULT_NODE_KINDS.get(REPORT_KIND)[0] is SimulationReport
    assert issubclass(DevelopmentSimulation, DevelopmentReplay)
    assert set(DevelopmentSimulation.outputs) == {
        "fills", "skipped", "refused", "cash", "metadata", "solves",
    }
    assert set(SimulationReport.outputs) == {"daily", "summary"}
    for value in (sim7["out"], sim7["report"]):
        assert json.loads(json.dumps(value)) == value


def test_simulate_inherits_the_development_replay_gates():
    check = DevelopmentSimulation.validate_params
    assert check(_sim_params()) == []
    assert any("deployment_eligible" in p for p in check(_sim_params(deployment_eligible=True)))
    assert any("caps" in p for p in check(_sim_params(caps="confirmed")))
    assert any("evidence_end" in p for p in check(_sim_params(evidence_end="2022/05/21")))
    assert any("fill_policy_sha256" in p for p in check(_sim_params(fill_policy_sha256="0" * 64)))
    bare = {k: v for k, v in _sim_params().items() if not k.startswith("cash_flow_policy")}
    assert any("cash_flow_policy" in p for p in check(bare))
    assert any("first_fold" in p for p in check(_sim_params(first=1)))
    assert any("last_fold" in p for p in check(_sim_params(first=3, last=2)))
    assert check({**_sim_params(), "extra": 1})


def test_segment_opening_cash_equals_previous_closing_cash(sim7):
    segments = sim7["out"]["metadata"]["segments"]
    assert [s["fold"] for s in segments] == [2, 3]
    first, second = segments
    assert first["opening_cash"] == "0"
    assert second["opening_cash"] == first["closing_cash"]
    rows = [row for row in sim7["out"]["cash"] if row["fold"] == 3]
    assert rows[0]["carried_in"] == first["closing_cash"]
    assert Decimal(rows[0]["booked"]) == Decimal(first["closing_cash"]) + Decimal("20")
    assert all(row["carried_in"] == "0" for row in rows[1:])
    # The closing cash is the seed, every contribution and every fill of segment 2.
    fills = [f for f in sim7["out"]["fills"] if f["fold"] == 2]
    assert fills, "segment 2 must trade for this test to mean anything"
    flows = sum(
        (Decimal(str(f["price"])) * f["qty"] - Decimal(str(f["fee"])))
        if f["side"] == "sell" else -(Decimal(str(f["price"])) * f["qty"] + Decimal(str(f["fee"])))
        for f in fills
    )
    contributed = sum(Decimal(row["contribution"]) for row in sim7["out"]["cash"] if row["fold"] == 2)
    assert Decimal(first["closing_cash"]) == contributed + flows


def test_total_contributions_equal_seed_plus_20_per_trading_day(sim7):
    days = sorted({_local_day(bar["asof_ms"]) for bar in sim7["bars"]})
    rows = sim7["out"]["cash"]
    assert [row["date"] for row in rows] == days
    assert len(days) == 2 * STEP
    contributions = [Decimal(row["contribution"]) for row in rows]
    assert contributions == [Decimal("1020")] + [Decimal("20")] * (len(days) - 1)
    assert Decimal(rows[-1]["contributions_to_date"]) == Decimal("1000") + 20 * len(days)
    daily = sim7["report"]["daily"]
    assert [row["date"] for row in daily] == days
    assert sim7["report"]["summary"]["total_contributed"] == pytest.approx(1000.0 + 20.0 * len(days))


def test_nav_minus_contributions_equals_windowbook_realised_when_flat(sim7):
    from fractions import Fraction

    from dskit.production.accounting import WindowBook
    from dskit.production.records import Fill

    book = WindowBook()
    by_day = {}
    for index, f in enumerate(sim7["out"]["fills"]):
        book.apply(Fill(
            fill_id=str(index), venue_ref="t", client_ref="t", instrument=f["symbol"], side=f["side"],
            qty=Decimal(str(f["qty"])), price=Decimal(str(f["price"])), fee=Decimal(str(f["fee"])),
            fee_currency="USD", liquidity="taker", status="final", ts_ms=f["asof_ms"], native=None,
        ))
        by_day[_local_day(f["asof_ms"])] = book.realised
    realised = Fraction(0)
    checked = 0
    for row in sim7["report"]["daily"]:
        realised = by_day.get(row["date"], realised)
        assert row["nav"] - row["contributions_to_date"] == pytest.approx(row["net_pnl"], abs=1e-9)
        assert row["nav_discrepancy"] == pytest.approx(0.0, abs=1e-6)
        assert row["replay_nav"] == pytest.approx(row["nav"], abs=1e-6)
        if row["unrealised_pnl"] == 0.0:
            assert row["net_pnl"] == pytest.approx(float(realised), abs=1e-9)
            assert row["realised_pnl"] == pytest.approx(float(realised), abs=1e-9)
            checked += 1
    assert checked == len(sim7["report"]["daily"])
    summary = sim7["report"]["summary"]
    assert summary["realised_pnl"] == pytest.approx(float(book.realised), abs=1e-9)
    assert summary["max_drawdown"] == pytest.approx(float(book.drawdown(lambda _s: None)), abs=1e-9)
    assert summary["fees"] == pytest.approx(sum(f["fee"] for f in sim7["out"]["fills"]), abs=1e-9)


def test_report_attribution_and_counts_sum_to_the_totals(sim7):
    summary = sim7["report"]["summary"]
    out = sim7["out"]
    assert sum(v["realised_pnl"] for v in summary["by_symbol"].values()) == pytest.approx(summary["realised_pnl"])
    assert sum(v["realised_pnl"] for v in summary["by_lead"].values()) == pytest.approx(summary["realised_pnl"])
    assert set(summary["by_symbol"]) <= {"LLY", "LRCX", "NOW"}
    assert {v["lead"] for v in summary["by_symbol"].values()} <= {1, 2}
    assert summary["fills_by_kind"] == {
        kind: sum(1 for f in out["fills"] if f["kind"] == kind) for kind in ("entry", "exit")
    }
    reasons = {}
    for row in out["refused"]:
        reasons[row["reason"]] = reasons.get(row["reason"], 0) + 1
    assert summary["refusals_by_reason"] == reasons
    assert summary["mio_refused"] == reasons.get("mio_refused", 0)
    skips = {}
    for row in out["skipped"]:
        skips[row["reason"]] = skips.get(row["reason"], 0) + 1
    assert summary["skips_by_reason"] == skips
    gross = sum(f["price"] * f["qty"] for f in out["fills"])
    assert sum(row["buy_notional"] + row["sell_notional"] for row in sim7["report"]["daily"]) == pytest.approx(gross)
    assert [s["release_id"] for s in summary["segments"]] == [r["release_id"] for r in sim7["published"]["releases"]]
    assert summary["segments"][0]["measured_coverage"] == sim7["published"]["releases"][0]["calibration"]["measured_coverage"]


def test_every_output_row_and_the_run_metadata_carry_the_disclosure(sim7):
    out, report = sim7["out"], sim7["report"]
    rows = (out["fills"] + out["skipped"] + out["refused"] + out["cash"] + out["solves"]
            + report["daily"])
    assert rows and out["fills"] and out["solves"]
    for row in rows:
        assert {k: row[k] for k in DISCLOSURE} == DISCLOSURE
        assert row["fold"] in (2, 3) and row["release_id"]
    for block in (out["metadata"], report["summary"], report["summary"]["metadata"]):
        assert {k: block[k] for k in DISCLOSURE} == DISCLOSURE
    assert out["metadata"]["evidence_end"] == EVIDENCE_END


def test_skipped_and_refused_rows_carry_the_disclosure(sim7, monkeypatch):
    fx, published = sim7["fx"], sim7["published"]
    release = published["releases"][0]
    first = min(b["decision_ts"] for b in published["bundles"] if b["release_id"] == release["release_id"])

    class Held(MioDecider):
        """The real decider; at the first tick the book is told LLY is still held."""

        def decide(self, asof_ms, portfolio):
            if asof_ms == first:
                portfolio = {**portfolio, "positions": {"LLY": 1}}
            return super().decide(asof_ms, portfolio)

    monkeypatch.setattr(simulation_module, "MioDecider", Held)
    inputs = _sim_inputs({**published, "releases": [release]}, list(sim7["bars"]), fx)
    # Coverage no calibration here attains: every lead group's solve is refused.
    inputs["mio"]["params"] = {**inputs["mio"]["params"], "uncertainty_min_coverage": 0.99}
    out = DevelopmentSimulation("simulate", _sim_params(last=2)).run(fx["ctx"], inputs)
    skips = [row for row in out["skipped"] if row["reason"] == "open_lot_at_decision"]
    refusals = [row for row in out["refused"] if row["reason"] == "mio_refused"]
    assert skips and refusals
    for row in skips + refusals:
        assert {k: row[k] for k in DISCLOSURE} == DISCLOSURE
        assert (row["fold"], row["release_id"]) == (2, release["release_id"])


def test_one_segment_equals_a_direct_equity_replay_of_the_same_bars(sim7):
    fx, published = sim7["fx"], sim7["published"]
    first = published["releases"][0]
    bars = [
        {**b, "halted": False} for b in sim7["bars"]
        if first["segment_start_ms"] <= b["asof_ms"] < first["segment_end_ms"]
    ]
    mio = _sim_inputs(published, [], fx)["mio"]
    decider = MioDecider(first, published["bundles"], mio, FILL_POLICY, fx["ctx"])
    direct = EquityReplay(FILL_POLICY, CASH_POLICY, decider=decider).run(bars)
    mine = [
        {k: v for k, v in f.items() if k not in DISCLOSURE and k not in ("fold", "release_id")}
        for f in sim7["out"]["fills"] if f["fold"] == 2
    ]
    assert mine == direct["fills"]


def test_each_segment_replays_only_its_own_bars_and_consumes_the_input(sim7, monkeypatch):
    fx, published = sim7["fx"], sim7["published"]
    seen = []
    real = EquityReplay.run

    def spy(self, bars, decisions=()):
        seen.append([b["asof_ms"] for b in bars])
        return real(self, bars, decisions)

    monkeypatch.setattr(EquityReplay, "run", spy)
    bars = list(sim7["bars"])
    DevelopmentSimulation("simulate", _sim_params(last=2, consume_bars=True)).run(
        fx["ctx"], _sim_inputs({**published, "releases": published["releases"][:1]}, bars, fx)
    )
    release = published["releases"][0]
    assert len(seen) == 1 and seen[0]
    assert all(release["segment_start_ms"] <= t < release["segment_end_ms"] for t in seen[0])
    assert bars == []


def test_simulate_refuses_releases_that_disagree_with_its_folds(sim7):
    fx, published = sim7["fx"], sim7["published"]
    with pytest.raises(ConfigError, match="fold"):
        DevelopmentSimulation("simulate", _sim_params(last=2)).run(
            fx["ctx"], _sim_inputs(published, list(sim7["bars"]), fx)
        )


def test_decision_after_evidence_end_refuses(sim7):
    fx, published = sim7["fx"], sim7["published"]
    early = (FIRST + timedelta(days=STEP * 3)).isoformat()
    with pytest.raises(ConfigError, match="evidence_end"):
        DevelopmentSimulation("simulate", _sim_params(evidence_end=early)).run(
            fx["ctx"], _sim_inputs(published, list(sim7["bars"]), fx)
        )


def test_bar_close_disagreeing_with_the_bundle_price_refuses(sim7):
    fx, published = sim7["fx"], sim7["published"]
    row = published["bundles"][0]["rows"][0]
    bars = [
        {**b, "close": b["close"] * 1.01}
        if (b["symbol"], b["asof_ms"]) == (row["entity"], row["decision_ts"]) else b
        for b in sim7["bars"]
    ]
    with pytest.raises(ConfigError, match="disagrees"):
        DevelopmentSimulation("simulate", _sim_params()).run(fx["ctx"], _sim_inputs(published, bars, fx))


def test_open_lots_at_segment_end_refuse(sim7, monkeypatch):
    fx, published = sim7["fx"], sim7["published"]
    release = published["releases"][0]
    last = max(b["decision_ts"] for b in published["bundles"] if b["release_id"] == release["release_id"])

    class Stub:
        def __init__(self, release, bundles, mio, fill_policy, ctx, keep_solves=True):
            self.skipped, self.refused = [], []

        def decide(self, asof_ms, portfolio):
            if asof_ms != last:
                return []
            return [{"symbol": "LLY", "asof_ms": asof_ms, "lead": 2, "qty": 1, "side": "buy"}]

    monkeypatch.setattr(simulation_module, "MioDecider", Stub)
    # LLY's tape ends one bar after the last decision: its lead-2 lot cannot exit.
    bars = [b for b in sim7["bars"] if not (b["symbol"] == "LLY" and b["asof_ms"] > last + 60_000)]
    with pytest.raises(ConfigError, match="open lot"):
        DevelopmentSimulation("simulate", _sim_params(last=2)).run(
            fx["ctx"], _sim_inputs({**published, "releases": published["releases"][:1]}, bars, fx)
        )


# --- the shipped pipeline documents ------------------------------------------

DOCUMENT = os.path.join(CONFIGS, "run-development-simulation.json")
SMOKE = os.path.join(CONFIGS, "run-development-simulation-smoke.json")


def _document_raw(path=DOCUMENT):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def test_report_is_a_separate_node_fed_only_by_wires():
    for path in (DOCUMENT, SMOKE):
        pipeline = _document_raw(path)["pipeline"]
        kinds = {key: spec["uses"] for key, spec in pipeline.items()}
        report = [key for key, kind in kinds.items() if kind == REPORT_KIND]
        assert len(report) == 1
        spec = pipeline[report[0]]
        assert set(spec.get("params") or {}) <= {"notes"}
        assert spec["inputs"] and all(
            isinstance(ref, str) and ref.startswith(("$simulate.", "$publish.")) for ref in spec["inputs"].values()
        )
        assert SimulationReport.role == "report"
        writes = {key: spec for key, spec in pipeline.items() if spec["uses"] in ("records-write", "table-write")}
        wired = {ref for spec in writes.values() for ref in spec["inputs"].values()}
        assert {"$simulate.fills", "$report.daily", "$report.summary"} <= wired
        assert sorted(set(kinds.values())) == sorted({
            "intraday_equities-bars", "concat", "intraday_equities-forecast-publisher",
            "intraday_equities-mio-decider", SIM_KIND, REPORT_KIND, "records-write", "table-write",
        })


def _utc_ms(day):
    return _ms(date.fromisoformat(day))


def test_bars_read_bounded_to_the_window(monkeypatch, tmp_path):
    import intraday_equities.nodes as nodes

    for path, start, end in (
        (DOCUMENT, "2022-09-09", "2025-10-17"),
        (SMOKE, "2022-09-09", "2022-11-11"),
    ):
        specs = [s for s in _document_raw(path)["pipeline"].values() if s["uses"] == "intraday_equities-bars"]
        assert sorted(s["params"]["source"] for s in specs) == ["alpaca-sip-split", "alpaca-sip-split-e"]
        for spec in specs:
            seen = {}

            def fake(root, source, stream, **kwargs):
                seen.update(kwargs, source=source)
                return []

            monkeypatch.setattr(nodes, "scan_stream", fake)
            monkeypatch.setattr(nodes, "dir_digest", lambda _path: "store")
            for name in ("_cached_key", "_cached_snap", "_cached_fingerprint"):
                monkeypatch.setattr(nodes.BarsFromStore, name, None)
            node = nodes.BarsFromStore("bars", {**spec["params"], "root": str(tmp_path)})
            node.run(NodeContext(name="t", asof="2026-09-24", run_dir=str(tmp_path)), {})
            assert seen["source"] == spec["params"]["source"]
            assert seen["since_ms"] == _utc_ms(start)
            admit = seen["admit"]
            inside = _utc_ms(end) - 86_400_000 + 15 * 3_600_000  # 11:00 ET on the last day
            ts = datetime.fromtimestamp(inside / 1000, tz=timezone.utc).isoformat()
            assert admit({"ts": ts}, inside) is True
            assert admit({"ts": ts}, _utc_ms(end)) is False
            assert admit({"ts": ts}, _utc_ms(end) + 3_600_000) is False


def _store_rows(bars):
    """Observation rows an onboarding store holds for ``bars`` (the ``_write_store`` shape)."""
    for bar in bars:
        ts = datetime.fromtimestamp(bar["asof_ms"] / 1000, tz=timezone.utc).isoformat()
        yield {
            "stream": "bars", "mode": "backfill", "kind": "observation", "effective_date": ts,
            "acquired_at": "2022-06-01T00:00:00+00:00",
            "data": {
                "symbol": bar["symbol"], "ts": ts, "open": bar["open"], "high": bar["open"],
                "low": bar["close"], "close": bar["close"], "volume": 100.0, "trade_count": 5,
                "vwap": bar["close"],
            },
        }


def test_document_runs_end_to_end_through_the_pipeline_driver(sim7, tmp_path, monkeypatch):
    from dskit.pipeline.__main__ import main

    fx, published = sim7["fx"], sim7["published"]
    raw = _document_raw()
    pipeline = raw["pipeline"]
    root = tmp_path / "ob"
    sources = {spec["params"]["source"]: key for key, spec in pipeline.items() if spec["uses"] == "intraday_equities-bars"}
    for source in sources:
        directory = root / "observations" / source / "acq-0001"
        directory.mkdir(parents=True)
        owned = {"alpaca-sip-split": {"LLY"}, "alpaca-sip-split-e": {"LRCX", "NOW"}}[source]
        with open(directory / "bars.jsonl", "w", encoding="utf-8") as handle:
            for row in _store_rows(b for b in sim7["bars"] if b["symbol"] in owned):
                handle.write(json.dumps(row, sort_keys=True) + "\n")
    releases = published["releases"]
    for key in sources.values():
        pipeline[key]["params"].update(
            root=str(root), start_ms=releases[0]["segment_start_ms"], end_ms=releases[-1]["segment_end_ms"],
        )
    pipeline["publish"]["params"].update({k: fx["params"][k] for k in pipeline["publish"]["params"] if k != "notes"})
    pipeline["simulate"]["params"].update(evidence_end=EVIDENCE_END, first_fold=2, last_fold=3)
    pipeline["decide"]["params"]["mio"] = MIO
    path = tmp_path / "run-development-simulation.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert main(["run", str(path), "--asof", "2022-06-01", "--adapter", "intraday_equities"]) == 0
    runs = [d for d in (tmp_path / "pipeline_runs").iterdir() if d.is_dir()]
    assert len(runs) == 1
    with open(runs[0] / "plan.json", encoding="utf-8") as handle:
        planned = json.dumps(json.load(handle))
    for key in pipeline:
        assert key in planned
    written = {
        p.name: p for p in (tmp_path / "pipeline_runs").iterdir() if p.is_file()
    }
    with open(written["development-simulation-summary.json"], encoding="utf-8") as handle:
        summary = json.load(handle)
    assert {k: summary[k] for k in DISCLOSURE} == DISCLOSURE
    assert [s["fold"] for s in summary["segments"]] == [2, 3]
    with open(written["development-simulation-daily.jsonl"], encoding="utf-8") as handle:
        daily = [json.loads(line) for line in handle]
    assert daily and all({k: row[k] for k in DISCLOSURE} == DISCLOSURE for row in daily)
    assert summary["trading_days"] == len(daily)
    with open(written["development-simulation-fills.jsonl"], encoding="utf-8") as handle:
        assert sum(1 for _ in handle) == sum(summary["fills_by_kind"].values())


# --- ADR-0185: a scheduled (biweekly-style) policy through the segmented simulation ---


@pytest.fixture(scope="module")
def sim_scheduled(tmp_path_factory):
    """Folds 2 and 3 under a 3-day scheduled policy: its phase must not reset at the 4-day boundary."""
    root = str(tmp_path_factory.mktemp("s7s"))
    fx = _build_fx(os.path.join(root, "fx"), count=4)
    fx["params"]["last_fold"] = 3
    published = _run(fx)
    bars = _thin_bars(published, fx)
    days = sorted({date.fromisoformat(_local_day(bar["asof_ms"])) for bar in bars})
    weekday = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")[
        (days[0].weekday() + 1) % 7
    ]
    path = os.path.join(root, "cash-flow-policy-scheduled.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({
            "kind": "scheduled", "currency": "USD", "initial_capital_amount": "10000",
            "contribution_amount": "500", "interval_days": 3, "first_weekday": weekday,
            "local_time": "09:30", "timezone": "America/New_York", "holiday_rule": "next_trading_day",
        }, fh)
    policy = CashFlowPolicy.from_path(path)
    node = DevelopmentSimulation("simulate", _sim_params(
        cash_flow_policy=path, cash_flow_policy_sha256=policy.digest(),
    ))
    out = node.run(fx["ctx"], _sim_inputs(published, list(bars), fx))
    ports = ("fills", "skipped", "refused", "cash", "metadata")
    report = SimulationReport("report", {}).run(
        fx["ctx"], {**{port: out[port] for port in ports}, "releases": published["releases"]}
    )
    return {"days": days, "out": out, "report": report}


def test_scheduled_contributions_follow_one_global_phase_across_segments(sim_scheduled):
    days = sim_scheduled["days"]
    phase = days[0] + timedelta(days=1)
    expected = {days[0]: Decimal("10000")}
    target = phase
    while target <= days[-1]:
        rolled = next(day for day in days if day >= target)
        expected[rolled] = expected.get(rolled, Decimal("0")) + Decimal("500")
        target += timedelta(days=3)
    rows = sim_scheduled["out"]["cash"]
    assert [row["date"] for row in rows] == [day.isoformat() for day in days]
    assert [Decimal(row["contribution"]) for row in rows] == [
        expected.get(day, Decimal("0")) for day in days
    ]
    assert Decimal(rows[-1]["contributions_to_date"]) == sum(expected.values())
    assert sim_scheduled["report"]["summary"]["total_contributed"] == pytest.approx(float(sum(expected.values())))


def test_scheduled_segment_carry_is_not_a_contribution(sim_scheduled):
    first, second = sim_scheduled["out"]["metadata"]["segments"]
    rows = [row for row in sim_scheduled["out"]["cash"] if row["fold"] == 3]
    assert rows[0]["carried_in"] == first["closing_cash"]
    assert Decimal(rows[0]["booked"]) - Decimal(rows[0]["carried_in"]) == Decimal(rows[0]["contribution"])
    for row in sim_scheduled["report"]["daily"]:
        assert row["nav"] - row["contributions_to_date"] == pytest.approx(row["net_pnl"], abs=1e-9)


# --- ADR-0185: the staged run's simulation stage binds the template to its own artifacts ---

_TEMPLATE = os.path.join(_child_root(), "configs", "run-retrain-simulation-template.json")


def _template_sha():
    import hashlib

    with open(_TEMPLATE, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _stage_dir(tmp_path):
    stages = tmp_path / "stages"
    stages.mkdir()
    (stages / "inventory.json").write_text(json.dumps({"outputs": {"manifest": {"m": 1}}}))
    (stages / "gates.json").write_text(json.dumps({"outputs": {"caps": [{"unit": "AAA"}]}}))
    return stages


def _retrained_params(**overrides):
    return {
        "template": "run-retrain-simulation-template.json", "template_sha256": _template_sha(),
        "inventory_stage": "inventory", "gates_stage": "gates", **overrides,
    }


def _retrained_ctx(tmp_path, stages):
    from types import SimpleNamespace

    return SimpleNamespace(
        source_path=os.path.join(_child_root(), "configs", "run-retrain-simulation.json"),
        artifact_dir=str(stages), asof="2026-02-28",
    )


def test_retrained_simulation_binds_the_template_to_this_runs_artifacts_and_runs_it(tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace

    from dskit.pipeline.planner import plan

    from intraday_equities.simulation import RetrainedSimulation

    stages = _stage_dir(tmp_path)
    seen = []

    def fake_run(document, asof=None):
        seen.append((document, asof))
        return SimpleNamespace(state="ran", exit_code=0, run_dir=str(tmp_path / "sim-run"),
                               outputs={"report": {"summary": {"final_nav": 1.0}}})

    monkeypatch.setattr("dskit.pipeline.driver.run_document", fake_run)
    stage = RetrainedSimulation("simulate", _retrained_params())
    out = stage.run(_retrained_ctx(tmp_path, stages), {"manifest": {"m": 1}, "caps": [{"unit": "AAA"}]})
    document, asof = seen[0]
    assert asof == "2026-02-28"
    plan(document)
    publish = document.pipeline["publish"].params
    for key, name in (("inventory_manifest", "inventory"), ("gates", "gates")):
        path = str((stages / f"{name}.json").resolve())
        assert publish[key] == path
        assert publish[f"{key}_sha256"] == hashlib.sha256(open(path, "rb").read()).hexdigest()
    assert publish["walk_root"] == os.getcwd()
    for key in ("write_fills", "write_refused", "write_daily", "write_summary"):
        assert document.pipeline[key].params["path"].startswith(str(stages / "simulate") + os.sep)
    assert document.pipeline["simulate"].params["cash_flow_policy"] == "configs/cash-flow-policy-biweekly.json"
    assert out["run_dir"] == str(tmp_path / "sim-run")
    assert out["summary"] == {"final_nav": 1.0}
    assert out["document_hash"] == document.hash


def test_retrained_simulation_refuses_a_template_that_moved(tmp_path):
    from intraday_equities.simulation import RetrainedSimulation

    stages = _stage_dir(tmp_path)
    stage = RetrainedSimulation("simulate", _retrained_params(template_sha256="0" * 64))
    with pytest.raises(ValueError, match="template"):
        stage.run(_retrained_ctx(tmp_path, stages), {"manifest": {"m": 1}, "caps": [{"unit": "AAA"}]})


def test_retrained_simulation_refuses_a_template_carrying_a_stale_pin(tmp_path):
    import hashlib

    from intraday_equities.simulation import RetrainedSimulation

    with open(_TEMPLATE, encoding="utf-8") as handle:
        obj = json.load(handle)
    obj["pipeline"]["publish"]["params"]["gates_sha256"] = "1" * 64
    path = tmp_path / "stale-template.json"
    path.write_text(json.dumps(obj))
    stages = _stage_dir(tmp_path)
    stage = RetrainedSimulation("simulate", _retrained_params(
        template=str(path), template_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    ))
    with pytest.raises(ValueError, match="BOUND-BY-STAGE"):
        stage.run(_retrained_ctx(tmp_path, stages), {"manifest": {"m": 1}, "caps": [{"unit": "AAA"}]})


def test_retrained_simulation_refuses_an_inventory_on_disk_that_is_not_the_one_handed_in(tmp_path):
    from intraday_equities.simulation import RetrainedSimulation

    stages = _stage_dir(tmp_path)
    stage = RetrainedSimulation("simulate", _retrained_params())
    with pytest.raises(ValueError, match="differs"):
        stage.run(_retrained_ctx(tmp_path, stages), {"manifest": {"m": 2}, "caps": [{"unit": "AAA"}]})


def test_retrained_simulation_refuses_a_run_that_did_not_finish(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from intraday_equities.simulation import RetrainedSimulation

    stages = _stage_dir(tmp_path)
    monkeypatch.setattr(
        "dskit.pipeline.driver.run_document",
        lambda document, asof=None: SimpleNamespace(state="error", exit_code=1, run_dir="x", outputs={}),
    )
    stage = RetrainedSimulation("simulate", _retrained_params())
    with pytest.raises(ValueError, match="error"):
        stage.run(_retrained_ctx(tmp_path, stages), {"manifest": {"m": 1}, "caps": [{"unit": "AAA"}]})


def test_retrained_simulation_default_denies_params():
    from intraday_equities.simulation import RetrainedSimulation

    assert RetrainedSimulation.validate_params(_retrained_params()) == []
    assert RetrainedSimulation.validate_params({**_retrained_params(), "extra": 1})
    assert RetrainedSimulation.validate_params(_retrained_params(template_sha256="nope"))


def test_cash_rows_still_refuse_an_unfunded_trading_day_under_the_daily_policy():
    from types import SimpleNamespace

    from intraday_equities.replay import ScheduledCashFlowPolicy

    day = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    replay = SimpleNamespace(booked_cash_flows=[
        {"effective_at_ms": int(day.timestamp() * 1000), "amount": "1020"},
    ])
    closes = {"2026-01-05": {"cash": 1.0, "gross_limit": 1.0, "mark_prices": {}},
              "2026-01-06": {"cash": 1.0, "gross_limit": 1.0, "mark_prices": {}}}
    recorder = SimpleNamespace(closes=closes)
    with pytest.raises(ConfigError, match="not funded"):
        DevelopmentSimulation._cash_rows(replay, recorder, None, Decimal("0"), TZ, CASH_POLICY)
    scheduled = ScheduledCashFlowPolicy({
        "kind": "scheduled", "currency": "USD", "initial_capital_amount": "10000",
        "contribution_amount": "500", "interval_days": 14, "first_weekday": "friday",
        "local_time": "09:30", "timezone": "America/New_York", "holiday_rule": "next_trading_day",
    })
    rows, _ = DevelopmentSimulation._cash_rows(replay, recorder, None, Decimal("0"), TZ, scheduled)
    assert [row["contribution"] for row in rows] == ["1020", "0"]


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda obj: obj["pipeline"].pop("publish"), "exactly one publisher"),
        (lambda obj: obj["pipeline"]["write_fills"]["params"].update(path="pipeline_runs/x.jsonl"), "must start with"),
    ],
    ids=["no-publisher", "unbound-writer"],
)
def test_retrained_simulation_refuses_a_template_it_cannot_bind_whole(tmp_path, mutate, expected):
    import hashlib

    from intraday_equities.simulation import RetrainedSimulation

    with open(_TEMPLATE, encoding="utf-8") as handle:
        obj = json.load(handle)
    mutate(obj)
    path = tmp_path / "template.json"
    path.write_text(json.dumps(obj))
    stages = _stage_dir(tmp_path)
    stage = RetrainedSimulation("simulate", _retrained_params(
        template=str(path), template_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    ))
    with pytest.raises(ValueError, match=expected):
        stage.run(_retrained_ctx(tmp_path, stages), {"manifest": {"m": 1}, "caps": [{"unit": "AAA"}]})


def test_retrained_simulation_refuses_gates_on_disk_that_are_not_the_caps_handed_in(tmp_path):
    from intraday_equities.simulation import RetrainedSimulation

    stages = _stage_dir(tmp_path)
    stage = RetrainedSimulation("simulate", _retrained_params())
    with pytest.raises(ValueError, match="gates on disk differ"):
        stage.run(_retrained_ctx(tmp_path, stages), {"manifest": {"m": 1}, "caps": [{"unit": "BBB"}]})


def test_retrained_simulation_refuses_two_publishers(tmp_path):
    import hashlib

    from intraday_equities.simulation import RetrainedSimulation

    with open(_TEMPLATE, encoding="utf-8") as handle:
        obj = json.load(handle)
    obj["pipeline"]["publish_again"] = json.loads(json.dumps(obj["pipeline"]["publish"]))
    path = tmp_path / "template.json"
    path.write_text(json.dumps(obj))
    stages = _stage_dir(tmp_path)
    stage = RetrainedSimulation("simulate", _retrained_params(
        template=str(path), template_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    ))
    with pytest.raises(ValueError, match="exactly one publisher"):
        stage.run(_retrained_ctx(tmp_path, stages), {"manifest": {"m": 1}, "caps": [{"unit": "AAA"}]})


def test_cash_rows_refuse_a_booked_date_the_replay_never_ticked():
    from types import SimpleNamespace

    day = datetime(2026, 1, 7, 14, 30, tzinfo=timezone.utc)
    replay = SimpleNamespace(booked_cash_flows=[{"effective_at_ms": int(day.timestamp() * 1000), "amount": "20"}])
    recorder = SimpleNamespace(closes={"2026-01-05": {"cash": 1.0, "gross_limit": 1.0, "mark_prices": {}}})
    with pytest.raises(ConfigError, match="not dates the replay ticked"):
        DevelopmentSimulation._cash_rows(replay, recorder, None, Decimal("0"), TZ, CASH_POLICY)


# --- ADR-0186: the per-minute publisher, decider and simulation --------------

MINUTE_KIND = "intraday_equities-minute-forecast-publisher"
MINUTE_SIM_KIND = "intraday_equities-minute-development-simulation"


@pytest.fixture
def mfx(tmp_path):
    """The three-fold walk with every fold's per-minute trade predictions pinned."""
    return _build_fx(str(tmp_path), minute=True)


def _mrun(fx):
    from intraday_equities.simulation import MinuteForecastPublisher

    return MinuteForecastPublisher("publish", fx["params"]).run(fx["ctx"], {})


def _closes(fx):
    """Each local date's last tape minute: the session close the decider reads."""
    stamps, _ = fx["tape"]
    out = {}
    for stamp in stamps:
        day = _local_day(int(stamp))
        out[day] = max(out.get(day, 0), int(stamp))
    return out


def _minute_decider(published, fx, closes=None, release=0, **overrides):
    from intraday_equities.simulation import MinuteMioDecider

    mio = MioDeciderNode("decide", {"mio": {**MIO, **overrides}}).run(
        fx["ctx"], {"releases": published["releases"]}
    )["mio"]
    return MinuteMioDecider(
        published["releases"][release], published["ticks"], mio, FILL_POLICY, fx["ctx"],
        _closes(fx) if closes is None else closes, TZ,
    )


def _mbook(fx, asof_ms, positions=None, pending=()):
    return {**_book(fx, asof_ms, positions), "pending": list(pending)}


def _open(release, day=0):
    """The first tape minute of the segment's ``day``-th day."""
    return release["segment_start_ms"] + day * 86_400_000 + OPEN_MS


def test_minute_publisher_releases_are_the_lattice_releases_plus_tick_constants(mfx):
    lattice = _run(mfx)["releases"]
    minute = _mrun(mfx)["releases"]
    extra = ("tick_constants", "label")
    assert _canon([{k: v for k, v in r.items() if k not in extra} for r in minute]) == _canon(lattice)
    for release in minute:
        assert release["label"]["label_residual"] == "SPY"
        for lead in release["lead_groups"]:
            constants = release["tick_constants"][str(lead)]
            members = sorted(s for s, held in release["lead_map"].items() if held == lead)
            assert sorted(constants["scenarios"]) == members
            assert all(len(draws) == len(constants["weights"]) for draws in constants["scenarios"].values())


def test_minute_publisher_ticks_every_traded_minute_of_the_segment(mfx):
    out = _mrun(mfx)
    assert "bundles" not in out
    release = out["releases"][0]
    stamps, prices = mfx["tape"]
    blocks = {(b["symbol"], b["lead"]): b for b in out["ticks"]}
    assert set(blocks) == set(release["lead_map"].items())
    window = [int(t) for t in stamps if release["segment_start_ms"] <= t < release["segment_end_ms"]]
    assert len(window) == STEP * 390
    for (symbol, lead), block in blocks.items():
        assert block["ts"] == window
        assert (block["fold"], block["release_id"]) == (2, release["release_id"])
        at = np.searchsorted(stamps, block["ts"])
        assert block["price"] == [float(p) for p in prices[symbol][at]]
        assert len(block["yhat"]) == len(block["sigma"]) == len(block["beta"]) == len(window)
    assert json.loads(json.dumps(out)) == out
    assert DEFAULT_NODE_KINDS.get(MINUTE_KIND)[0].__name__ == "MinuteForecastPublisher"


def test_minute_bundle_rows_equal_the_lattice_rows_at_every_lattice_tick(mfx):
    lattice = _run(mfx)
    decider = _minute_decider(_mrun(mfx), mfx)
    for bundle in lattice["bundles"]:
        names = [row["entity"] for row in bundle["rows"]]
        rows = decider._bundle_rows(bundle["decision_ts"], bundle["lead"], names)
        assert _canon(rows) == _canon(bundle["rows"])
    assert lattice["bundles"]


def test_minute_publisher_refuses_trade_yhat_that_disagrees_with_the_scored_yhat(mfx):
    target = sorted(mfx["data"][(2, 2)]["LLY"][0])[3]

    def moved(i, n, lead, stamps, yhat):
        return stamps, [v + 0.5 if (i, n, lead, t) == (2, "LLY", 2, target) else v for t, v in zip(stamps, yhat)]

    _write_trade(mfx, moved)
    _write_inventory(mfx)
    with pytest.raises(ValueError, match="trade yhat"):
        _mrun(mfx)


def test_minute_publisher_refuses_a_scored_minute_with_no_trade_row(mfx):
    target = sorted(mfx["data"][(2, 1)]["NOW"][0])[5]

    def dropped(i, n, lead, stamps, yhat):
        keep = [k for k, t in enumerate(stamps) if (i, n, lead, t) != (2, "NOW", 1, target)]
        return [stamps[k] for k in keep], [yhat[k] for k in keep]

    _write_trade(mfx, dropped)
    _write_inventory(mfx)
    with pytest.raises(ValueError, match="trade yhat"):
        _mrun(mfx)


def test_minute_publisher_refuses_an_inventory_without_trade_pins(mfx):
    mfx["minute"] = False
    _write_inventory(mfx)
    with pytest.raises(ValueError, match="trade prediction"):
        _mrun(mfx)


def test_minute_publisher_refuses_a_trade_file_that_moved_after_the_inventory(mfx):
    _write_trade(mfx, lambda i, n, lead, stamps, yhat: (stamps, [v + 1.0 for v in yhat]))
    with pytest.raises(ValueError, match="hash changed"):
        _mrun(mfx)


def test_minute_walk_never_reads_label_tapes_from_a_trade_cache(mfx, monkeypatch):
    import intraday_equities.feature_cache as feature_cache

    verified = []
    real = feature_cache.verify_feature_cache

    def spy(path, sha):
        verified.append(os.path.basename(path))
        return real(path, sha)

    monkeypatch.setattr(feature_cache, "verify_feature_cache", spy)
    config = json.load(open(os.path.join(mfx["runs"][2], "config.json"), encoding="utf-8"))
    assert f"{TRADE_CACHE_PREFIX}a" in config["pipeline"]
    out = _mrun(mfx)
    assert out["ticks"] and verified and "trade" not in verified
    # The lattice walk has no such filter to lean on: its folds carry no trade cache.
    lattice = _build_fx(os.path.join(os.path.dirname(mfx["runs"][0]), "..", "lattice"))
    assert not any(k.startswith(TRADE_CACHE_PREFIX) for k in json.load(
        open(os.path.join(lattice["runs"][2], "config.json"), encoding="utf-8"))["pipeline"])


def test_minute_publisher_reads_trade_rows_without_y(mfx, monkeypatch):
    import pyarrow.parquet as pq

    requested = []
    real = pq.read_table

    def spy(path, **kwargs):
        requested.append((os.path.basename(path), kwargs.get("columns")))
        return real(path, **kwargs)

    monkeypatch.setattr(pq, "read_table", spy)
    _mrun(mfx)
    trade = [cols for name, cols in requested if name == TRADE_PREDICTIONS_FILE]
    assert trade and all(cols is not None and "y" not in cols for cols in trade)


def test_minute_decider_leaves_out_a_name_with_no_tick_at_that_minute(mfx, monkeypatch):
    seen = []
    real = EquityKellyMIO.run

    def spy(self, ctx, inputs):
        seen.append(sorted(row["entity"] for row in inputs["bundle"]))
        return real(self, ctx, inputs)

    monkeypatch.setattr(EquityKellyMIO, "run", spy)
    published = json.loads(json.dumps(_mrun(mfx)))
    t = _open(published["releases"][0]) + 11 * 60_000
    block = next(b for b in published["ticks"] if b["symbol"] == "LLY")
    k = block["ts"].index(t)
    for column in ("ts", "price", "yhat", "sigma", "beta"):
        del block[column][k]
    decider = _minute_decider(published, mfx)
    decider.decide(t, _mbook(mfx, t))
    # LLY printed no tick at t: it is simply not a candidate there -- not skipped, not sized.
    assert seen == [["NOW"], ["LRCX"]]
    assert decider.skipped == []


def test_minute_publisher_refuses_trade_rows_out_of_time_order(mfx):
    def shuffled(i, n, lead, stamps, yhat):
        if (i, n, lead) != (2, "LRCX", 2):
            return stamps, yhat
        return [stamps[1], stamps[0], *stamps[2:]], [yhat[1], yhat[0], *yhat[2:]]

    _write_trade(mfx, shuffled)
    _write_inventory(mfx)
    with pytest.raises(ValueError, match="time-ordered"):
        _mrun(mfx)


def test_minute_publisher_refuses_an_unpinned_trade_file_in_a_fold(mfx):
    extra = os.path.join(mfx["runs"][1], "artifacts", "scan_extra")
    with PredictionWriter(extra, list(NAMES), filename=TRADE_PREDICTIONS_FILE) as writer:
        writer.append("LLY", 2, [1], [float("nan")], [0.1], float("nan"))
    with pytest.raises(ValueError, match="trade prediction inventory drifted"):
        _mrun(mfx)


def test_minute_publisher_ticks_only_the_segment_even_if_trade_rows_start_earlier(mfx):
    first = _splits(2)["val_start_ms"]
    early = [first - 86_400_000 + OPEN_MS + i * 60_000 for i in range(3)]

    def widened(i, n, lead, stamps, yhat):
        return (early + stamps, [9.0] * len(early) + yhat) if i == 2 else (stamps, yhat)

    _write_trade(mfx, widened)
    _write_inventory(mfx)
    out = _mrun(mfx)
    assert all(min(block["ts"]) >= first for block in out["ticks"])
    assert all(9.0 not in block["yhat"][:3] for block in out["ticks"])


def test_minute_decider_refuses_a_tick_block_that_is_not_an_admitted_unit(mfx):
    published = json.loads(json.dumps(_mrun(mfx)))
    block = next(b for b in published["ticks"] if b["symbol"] == "LLY")
    block["lead"] = 1
    with pytest.raises(ValueError, match="not one admitted unit"):
        _minute_decider(published, mfx)


def test_minute_decider_opens_nothing_on_a_date_without_a_known_close(mfx):
    published = _mrun(mfx)
    decider = _minute_decider(published, mfx, closes={})
    t = _open(published["releases"][0]) + 20 * 60_000
    decider.decide(t, _mbook(mfx, t))
    assert decider.solves == [] and {r["reason"] for r in decider.skipped} == {"exit_after_close"}


def test_minute_window_gate_ignores_an_empty_tick_block():
    from intraday_equities.simulation import MinuteDevelopmentSimulation

    rows = MinuteDevelopmentSimulation._decision_rows([{"ts": []}, {"ts": [5, 9, 7]}])
    assert rows == [{"asof_ms": 9}]


def test_minute_decider_solves_at_a_minute_off_the_lattice(mfx):
    published = _mrun(mfx)
    decider = _minute_decider(published, mfx)
    t = _open(published["releases"][0]) + 7 * 60_000
    decider.decide(t, _mbook(mfx, t))
    assert {(row["asof_ms"], row["lead"]) for row in decider.solves} == {(t, 1), (t, 2)}


def test_minute_decider_skips_held_and_pending_units_and_reserves_pending_cash(mfx, monkeypatch):
    from intraday_equities.nodes_capital import SchwabCostModel

    seen = []
    real = EquityKellyMIO.run

    def spy(self, ctx, inputs):
        seen.append(([row["entity"] for row in inputs["bundle"]], inputs["portfolio"]["cash"]))
        return real(self, ctx, inputs)

    monkeypatch.setattr(EquityKellyMIO, "run", spy)
    published = _mrun(mfx)
    decider = _minute_decider(published, mfx)
    t = _open(published["releases"][0]) + 100 * 60_000
    queued = {"symbol": "NOW", "lead": 1, "qty": 3, "side": "buy", "decision_ms": t - 60_000}
    decider.decide(t, _mbook(mfx, t, positions={"LLY": 4}, pending=[queued]))
    assert sorted((r["symbol"], r["lead"], r["reason"]) for r in decider.skipped) == [
        ("LLY", 2, "open_lot_at_decision"), ("NOW", 1, "pending_entry_at_decision"),
    ]
    block = next(b for b in published["ticks"] if b["symbol"] == "NOW")
    price = block["price"][block["ts"].index(t - 60_000)]
    costs = SchwabCostModel({name: getattr(FILL_POLICY, name) for name in SchwabCostModel._PARAMS})
    reserved = 3 * price + 3 * costs.buy_per_share("NOW", price)
    assert seen == [(["LRCX"], pytest.approx(1020.0 - reserved))]


def test_minute_decider_refuses_a_pending_entry_it_did_not_size(mfx):
    published = _mrun(mfx)
    decider = _minute_decider(published, mfx)
    t = _open(published["releases"][0]) + 100 * 60_000
    stray = {"symbol": "NOW", "lead": 1, "qty": 3, "side": "buy", "decision_ms": t - 30}
    with pytest.raises(ValueError, match="pending"):
        decider.decide(t, _mbook(mfx, t, pending=[stray]))


def test_minute_decider_opens_no_lot_that_would_exit_after_the_close(mfx):
    published = _mrun(mfx)
    decider = _minute_decider(published, mfx)
    close = _closes(mfx)[_local_day(_open(published["releases"][0]))]
    t = close - 2 * 60_000
    decider.decide(t, _mbook(mfx, t))
    # Lead 1 fills at t+1 and exits at t+2 = the close: allowed. Lead 2 would exit after it.
    assert sorted((r["symbol"], r["reason"]) for r in decider.skipped) == [
        ("LLY", "exit_after_close"), ("LRCX", "exit_after_close"),
    ]
    assert {row["lead"] for row in decider.solves} == {1}
    later = _minute_decider(published, mfx)
    later.decide(close - 60_000, _mbook(mfx, close - 60_000))
    assert later.solves == [] and len(later.skipped) == 3


def test_minute_close_rule_reads_the_fill_offset_from_the_fill_policy(mfx):
    from intraday_equities.simulation import MinuteMioDecider

    published = _mrun(mfx)
    with open(os.path.join(CONFIGS, "fill-policy.json"), encoding="utf-8") as handle:
        later_fill = FillPolicy({**json.load(handle), "fill_bar_offset": 2})
    mio = MioDeciderNode("decide", {"mio": MIO}).run(mfx["ctx"], {"releases": published["releases"]})["mio"]
    decider = MinuteMioDecider(
        published["releases"][0], published["ticks"], mio, later_fill, mfx["ctx"], _closes(mfx), TZ,
    )
    close = _closes(mfx)[_local_day(_open(published["releases"][0]))]
    t = close - 3 * 60_000
    decider.decide(t, _mbook(mfx, t))
    # Fill two bars later: lead 1 exits at t+3 = the close (allowed), lead 2 after it.
    assert sorted((r["symbol"], r["reason"]) for r in decider.skipped) == [
        ("LLY", "exit_after_close"), ("LRCX", "exit_after_close"),
    ]
    assert {row["lead"] for row in decider.solves} == {1}


def test_minute_decision_at_t_reads_no_tick_after_t(mfx):
    published = _mrun(mfx)
    t = _open(published["releases"][0]) + 40 * 60_000
    first = _minute_decider(published, mfx)
    orders = first.decide(t, _mbook(mfx, t))
    moved = json.loads(json.dumps(published))
    for block in moved["ticks"]:
        for k, stamp in enumerate(block["ts"]):
            if stamp > t:
                block["yhat"][k] += 5.0
                block["price"][k] *= 1.5
                block["sigma"][k] *= 3.0
                block["beta"][k] += 1.0
    second = _minute_decider(moved, mfx)
    assert second.decide(t, _mbook(mfx, t)) == orders
    strip = [{k: v for k, v in row.items() if k != "seconds"} for row in first.solves]
    assert [{k: v for k, v in row.items() if k != "seconds"} for row in second.solves] == strip
    assert strip, "the decision must solve for this test to mean anything"


def _minute_bars(published, fx, days=(0,), head=30, tail=20):
    """Each admitted name's bars in the first ``head`` and last ``tail`` minutes of the given segment days."""
    stamps, prices = fx["tape"]
    keep = set()
    for release in published["releases"]:
        for day in days:
            start = _open(release, day)
            keep.update(start + i * 60_000 for i in range(head))
            keep.update(start + i * 60_000 for i in range(390 - tail, 390))
    return [
        {"symbol": s, "asof_ms": int(t), "open": float(p), "close": float(p), "halted": False}
        for s in published["releases"][0]["survivors"]
        for t, p in zip(stamps, prices[s])
        if int(t) in keep
    ]


@pytest.fixture(scope="module")
def msim(tmp_path_factory):
    """One funded minute replay of segment 2's first day (head and tail of the session)."""
    fx = _build_fx(str(tmp_path_factory.mktemp("m6")), minute=True)
    published = _mrun(fx)
    bars = [b for b in _minute_bars(published, fx) if b["asof_ms"] < published["releases"][0]["segment_end_ms"]]
    spy = _Spy(_minute_decider(published, fx))
    out = EquityReplay(FILL_POLICY, CASH_POLICY, decider=spy).run(bars)
    return {"fx": fx, "published": published, "bars": bars, "spy": spy, "out": out}


def test_minute_replay_consults_the_decider_every_minute_it_ticks(msim):
    ticks = sorted({b["asof_ms"] for b in msim["bars"]})
    assert [asof for asof, _, _ in msim["spy"].calls] == ticks
    solved = {row["asof_ms"] for row in msim["spy"].inner.solves}
    off_lattice = {t for t in solved if (t - OPEN_MS) % 1_800_000}
    assert len(off_lattice) > 20


def test_minute_entries_fill_next_bar_and_exit_at_fill_plus_lead(msim):
    by_symbol = _by_symbol(msim["bars"])
    fills = msim["out"]["fills"]
    entries = [f for f in fills if f["kind"] == "entry"]
    assert entries, "the fixture must trade for this test to mean anything"
    for entry in entries:
        seq = by_symbol[entry["symbol"]]
        times = [b["asof_ms"] for b in seq]
        assert times[times.index(entry["decision_ms"]) + 1] == entry["asof_ms"]
        due = seq[times.index(entry["asof_ms"]) + entry["lead"]]
        assert any(
            (f["kind"], f["symbol"], f["asof_ms"], f["qty"]) == ("exit", entry["symbol"], due["asof_ms"], entry["qty"])
            for f in fills
        )
        close = _closes(msim["fx"])[_local_day(entry["decision_ms"])]
        assert entry["decision_ms"] + (1 + entry["lead"]) * 60_000 <= close


def test_minute_replay_never_repeats_an_open_or_queued_lot(msim):
    assert not [r for r in msim["out"]["refused"] if r["reason"] == "same_lead_open"]
    held = [r for r in msim["spy"].inner.skipped if r["reason"] == "open_lot_at_decision"]
    assert held, "a lot must be open at a later minute for this test to mean anything"


def _minute_sim_inputs(published, bars, fx):
    mio = MioDeciderNode("decide", {"mio": MIO}).run(fx["ctx"], {"releases": published["releases"]})["mio"]
    return {"bars": bars, "releases": published["releases"], "ticks": published["ticks"], "mio": mio}


@pytest.fixture(scope="module")
def msim7(tmp_path_factory):
    """Two funded minute segments (folds 2 and 3) through the minute simulate node and the report."""
    from intraday_equities.simulation import MinuteDevelopmentSimulation

    fx = _build_fx(str(tmp_path_factory.mktemp("m7")), count=4, minute=True)
    fx["params"]["last_fold"] = 3
    published = _mrun(fx)
    bars = _minute_bars(published, fx)
    node = MinuteDevelopmentSimulation("simulate", _sim_params(keep_solves=False))
    out = node.run(fx["ctx"], _minute_sim_inputs(published, list(bars), fx))
    ports = ("fills", "skipped", "refused", "cash", "metadata")
    report = SimulationReport("report", {}).run(
        fx["ctx"], {**{port: out[port] for port in ports}, "releases": published["releases"]}
    )
    return {"fx": fx, "published": published, "bars": bars, "out": out, "report": report}


def test_minute_simulate_is_a_registered_development_simulation(msim7):
    from intraday_equities.simulation import MinuteDevelopmentSimulation

    assert DEFAULT_NODE_KINDS.get(MINUTE_SIM_KIND)[0] is MinuteDevelopmentSimulation
    assert issubclass(MinuteDevelopmentSimulation, DevelopmentSimulation)
    assert MinuteDevelopmentSimulation.outputs == DevelopmentSimulation.outputs
    assert json.loads(json.dumps(msim7["out"])) == msim7["out"]
    assert MinuteDevelopmentSimulation.validate_params(_sim_params()) == []
    assert MinuteDevelopmentSimulation.validate_params(_sim_params(deployment_eligible=True))
    assert MinuteDevelopmentSimulation.validate_params(_sim_params(keep_solves="no"))
    node = MinuteDevelopmentSimulation("simulate", _sim_params())
    inputs = _minute_sim_inputs(msim7["published"], [], msim7["fx"])
    assert node.validate_inputs(inputs) == []
    assert any("ticks" in p for p in node.validate_inputs({**inputs, "ticks": None}))


def test_minute_simulate_trades_off_the_lattice_in_both_segments(msim7):
    fills = msim7["out"]["fills"]
    assert {f["fold"] for f in fills} == {2, 3}
    entries = [f for f in fills if f["kind"] == "entry"]
    assert [f for f in entries if (f["decision_ms"] - OPEN_MS) % 1_800_000]
    assert msim7["report"]["summary"]["max_abs_nav_discrepancy"] == pytest.approx(0.0, abs=1e-6)


def test_minute_decider_without_kept_solves_holds_no_rows_but_counts_them(mfx):
    from intraday_equities.simulation import MinuteMioDecider

    published = _mrun(mfx)
    mio = MioDeciderNode("decide", {"mio": MIO}).run(mfx["ctx"], {"releases": published["releases"]})["mio"]
    decider = MinuteMioDecider(
        published["releases"][0], published["ticks"], mio, FILL_POLICY, mfx["ctx"], _closes(mfx), TZ,
        keep_solves=False,
    )
    start = _open(published["releases"][0])
    for minute in (5, 6, 7):
        decider.decide(start + minute * 60_000, _mbook(mfx, start + minute * 60_000))
    assert decider.solves == [] and decider.n_solves == 6 and decider.solve_seconds > 0.0


def test_minute_walk_keeps_only_the_latest_folds_label_arrays(mfx):
    from intraday_equities.simulation import _MinuteWalk

    walk = _MinuteWalk(mfx["params"])
    try:
        first = walk.market(1)
        assert walk.market(1) is first
        walk.market(2)
        assert list(walk._markets) == [2]
    finally:
        walk.close()


def test_minute_simulate_without_kept_solves_counts_them_in_the_metadata(msim7, monkeypatch):
    from intraday_equities.simulation import MinuteDevelopmentSimulation

    built = _spy_deciders(monkeypatch, "MinuteMioDecider")
    fx, published = msim7["fx"], msim7["published"]
    MinuteDevelopmentSimulation("simulate", _sim_params(keep_solves=False)).run(
        fx["ctx"], _minute_sim_inputs(published, list(msim7["bars"]), fx)
    )
    assert len(built) == 2 and all(d.solves == [] and d.n_solves > 0 for d in built)
    out = msim7["out"]
    assert out["solves"] == []
    counted = out["metadata"]["solves"]
    assert counted["count"] > 0 and counted["seconds"] > 0.0
    skips = Counter(row["reason"] for row in out["skipped"])
    assert skips["exit_after_close"] > 0


def _spy_deciders(monkeypatch, name):
    """Record every decider the simulation builds under ``simulation_module.<name>``."""
    built = []
    real = getattr(simulation_module, name)

    class Recording(real):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            built.append(self)

    monkeypatch.setattr(simulation_module, name, Recording)
    return built


def test_development_simulation_keep_solves_false_drops_the_rows_and_keeps_the_count(sim7, monkeypatch):
    fx, published = sim7["fx"], sim7["published"]
    built = _spy_deciders(monkeypatch, "MioDecider")
    out = DevelopmentSimulation("simulate", _sim_params(keep_solves=False)).run(
        fx["ctx"], _sim_inputs(published, list(sim7["bars"]), fx)
    )
    assert out["solves"] == [] and out["fills"] == sim7["out"]["fills"]
    assert out["metadata"]["solves"]["count"] == len(sim7["out"]["solves"]) > 0
    assert "solves" not in sim7["out"]["metadata"]
    # The deciders themselves never held the rows: the flag bounds memory, not just output.
    assert len(built) == 2 and all(d.solves == [] and d.n_solves > 0 for d in built)


def _carry_stub(target):
    """A minute decider stub: one LLY lead-2 buy at ``target``, nothing else."""

    class Stub:
        def __init__(self, release, ticks, mio, fill_policy, ctx, closes, tz, keep_solves=True):
            self.skipped, self.refused, self.solves = [], [], []
            self.n_solves, self.solve_seconds = 0, 0.0

        def decide(self, asof_ms, portfolio):
            if asof_ms != target:
                return []
            return [{"symbol": "LLY", "asof_ms": asof_ms, "lead": 2, "qty": 1, "side": "buy"}]

    return Stub


def test_a_lot_open_at_a_release_boundary_carries_into_the_next_release(msim7, monkeypatch):
    from intraday_equities.simulation import MinuteDevelopmentSimulation

    fx, published = msim7["fx"], msim7["published"]
    first, second = published["releases"]
    lly = [b["asof_ms"] for b in msim7["bars"] if b["symbol"] == "LLY"]
    before = [t for t in lly if t < first["segment_end_ms"]]
    # Decide at LLY's second-to-last bar of release 2: fill at its last bar, owe 2 more.
    target = before[-2]
    monkeypatch.setattr(simulation_module, "MinuteMioDecider", _carry_stub(target))
    out = MinuteDevelopmentSimulation("simulate", _sim_params()).run(
        fx["ctx"], _minute_sim_inputs(published, list(msim7["bars"]), fx)
    )
    fills = [(f["fold"], f["kind"], f["asof_ms"]) for f in out["fills"] if f["symbol"] == "LLY"]
    after = [t for t in lly if t >= second["segment_start_ms"]]
    assert fills == [(2, "entry", before[-1]), (3, "exit", after[1])]
    assert not [r for r in out["refused"] if r["reason"] == "expiry_past_tape"]
    segments = out["metadata"]["segments"]
    assert segments[0]["carried_out"] == [{"symbol": "LLY", "lead": 2, "qty": 1, "side": "buy", "exit_in": 1}]
    assert segments[1]["opening_cash"] == segments[0]["closing_cash"]
    assert out["metadata"]["open_at_evidence_end"] == []


def test_a_lot_open_at_the_evidence_end_stays_marked_and_is_listed(msim7, monkeypatch):
    from intraday_equities.simulation import MinuteDevelopmentSimulation

    fx, published = msim7["fx"], msim7["published"]
    lly = [b["asof_ms"] for b in msim7["bars"] if b["symbol"] == "LLY"]
    target = lly[-2]
    monkeypatch.setattr(simulation_module, "MinuteMioDecider", _carry_stub(target))
    out = MinuteDevelopmentSimulation("simulate", _sim_params()).run(
        fx["ctx"], _minute_sim_inputs(published, list(msim7["bars"]), fx)
    )
    assert out["metadata"]["open_at_evidence_end"] == [
        {"symbol": "LLY", "lead": 2, "qty": 1, "side": "buy", "exit_in": 1},
    ]
    last = out["cash"][-1]
    lly_close = next(b["close"] for b in msim7["bars"] if b["symbol"] == "LLY" and b["asof_ms"] == lly[-1])
    assert last["nav_close"] == pytest.approx(last["cash_close"] + lly_close)


@pytest.mark.parametrize("which", [0, -1], ids=["first-minute", "last-minute"])
def test_minute_simulate_refuses_a_bar_close_that_disagrees_with_the_tick_price(msim7, which):
    from intraday_equities.simulation import MinuteDevelopmentSimulation

    fx, published = msim7["fx"], msim7["published"]
    end = published["releases"][0]["segment_end_ms"]
    target = [b for b in msim7["bars"] if b["symbol"] == "LRCX" and b["asof_ms"] < end][which]
    bars = [{**b, "close": b["close"] * 1.01} if b is target else b for b in msim7["bars"]]
    with pytest.raises(ConfigError, match="disagrees"):
        MinuteDevelopmentSimulation("simulate", _sim_params()).run(
            fx["ctx"], _minute_sim_inputs(published, bars, fx)
        )


def test_minute_simulate_refuses_a_tick_after_the_evidence_end(msim7):
    from intraday_equities.simulation import MinuteDevelopmentSimulation

    fx, published = msim7["fx"], msim7["published"]
    early = (FIRST + timedelta(days=STEP * 3)).isoformat()
    with pytest.raises(ConfigError, match="evidence_end"):
        MinuteDevelopmentSimulation("simulate", _sim_params(evidence_end=early)).run(
            fx["ctx"], _minute_sim_inputs(published, list(msim7["bars"]), fx)
        )
