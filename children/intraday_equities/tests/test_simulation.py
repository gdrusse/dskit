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
from datetime import date, datetime, timedelta, timezone

from decimal import Decimal

import numpy as np
import pytest
from dskit.pipeline.document import PipelineDocument
from dskit.pipeline.false_signal import GrenanderLocalFdr
from dskit.pipeline.node import DEFAULT_NODE_KINDS, ConfigError, NodeContext, class_ref
from dskit.pipeline.predictions import PredictionWriter
from dskit.pipeline.uncertainty_intake import DecisionDemand, admission_problems

import intraday_equities.simulation as simulation_module
from intraday_equities.feature_cache import write_feature_cache
from intraday_equities.final_gates import DEVELOPMENT_EVIDENCE_SCOPE
from intraday_equities.forecast_bundle import ConfirmedCaps, ForecastBundle
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
    return _build_fx(str(tmp_path))


def _build_fx(root, count=COUNT):
    """Write the pinned ``count``-fold walk under ``root``; return its handles."""
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
    spec = Universe("universe", {"path": UNIVERSE}).run(None, {})["spec"]
    universe_sha = Universe("universe", {"path": UNIVERSE}).fingerprint()["sha256"]
    rng = np.random.default_rng(11)
    fx = {"runs": [], "data": {}, "tape": (stamps, prices), "inventory": os.path.join(root, "inv", "stages", "inventory.json")}
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
    return FillPolicy({**raw, "spread_bps": 0.0, "taf_per_share": 0.0, "sec31_bps": 0.0})


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
        "gross_limit": 910.0 + 10 * 11.5,
    }
    # At t3 the lot exits at the next bar (index 4 = fill + lead): no longer a
    # position for the decider, still part of NAV.
    assert stub.seen[_minute(3)]["positions"] == {}
    assert stub.seen[_minute(3)]["gross_limit"] == 910.0 + 10 * 13.5
    assert stub.seen[_minute(4)]["cash"] == 910.0 + 10 * 14.0
    assert [(f["kind"], f["asof_ms"], f["price"]) for f in out["fills"]] == [
        ("entry", _minute(1), 11.0), ("exit", _minute(4), 14.0),
    ]


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
    for bound in ({"spread_bps": 2.2}, {"cap_artifact_sha256": "a" * 64}, {"bundle_producer_node": "x"}):
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
    assert set(DevelopmentSimulation.outputs) == {"fills", "skipped", "refused", "cash", "metadata"}
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
    rows = out["fills"] + out["skipped"] + out["refused"] + out["cash"] + report["daily"]
    assert rows and out["fills"]
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
        def __init__(self, release, bundles, mio, fill_policy, ctx):
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
